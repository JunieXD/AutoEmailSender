# 匹配分析与草稿材料解耦实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让匹配分析使用个人页当前默认材料，让草稿生成和改写完全独立于匹配结果，并兼容旧版本历史数据。

**架构：** 保留现有 `EmailTask` 作为工作区和联系流程状态载体，但匹配 runtime 不再读取 `EmailTask.primary_material_id`。新增 `match_analysis_runs.primary_material_id` 记录每次匹配实际使用的身份默认材料；`EmailTask.primary_material_id` 只保留为 AI 写信参考材料。草稿 prompt 删除所有 `current_match` / `match_score` 上下文。

**技术栈：** FastAPI、SQLAlchemy async ORM、Alembic、SQLite、unittest、React 19、Vite、Vitest。

---

## 文件结构

- 修改 `backend/app/models/match_analysis_run.py`：新增 `primary_material_id` 关系字段。
- 创建 `backend/alembic/versions/20260611_match_material_decoupling.py`：增加运行记录材料字段，回填历史运行记录，保守补齐身份默认材料。
- 修改 `backend/test/test_database_schema.py`：覆盖新列、索引、迁移回填和默认材料补齐。
- 修改 `backend/app/services/task_runtime.py`：匹配时读取身份当前默认材料；草稿路径停止构造和传递匹配上下文。
- 修改 `backend/app/services/match_analysis_job_runtime.py`：批量匹配不再为了匹配目的补任务材料。
- 修改 `backend/app/services/llm_runtime.py`：草稿生成和改写 prompt 不再接收或输出匹配上下文。
- 修改 `backend/test/test_match_analysis_runtime.py`：覆盖匹配材料来源、旧任务材料清空后的重算、运行记录材料 id。
- 修改 `backend/test/test_match_analysis_jobs.py`：覆盖批量匹配执行时使用身份当前默认材料。
- 修改 `backend/test/test_llm_runtime.py`：覆盖草稿 prompt 不包含匹配结果。
- 修改 `backend/test/test_api_endpoints.py`：覆盖 API 回归场景。
- 修改 `frontend/src/pages/WorkspacePage.tsx`：计算匹配按钮不依赖任务材料，提示依赖身份默认材料。
- 修改 `frontend/src/components/organisms/WorkspaceComposerDock.tsx`：文案从“用于匹配的材料”调整为“AI 写信参考材料”。
- 修改 `frontend/test/WorkspacePageNextStep.test.tsx`、`frontend/src/pages/WorkspacePage.test.tsx`、`frontend/test/WorkspaceComposerDockCopy.test.tsx`：覆盖前端行为和文案。
- 修改 `website/docs/matching.md`、`website/docs/profile.md`：更新用户文档中的材料语义。

---

### 任务 1：新增匹配运行材料字段和历史数据迁移

**文件：**
- 修改：`backend/app/models/match_analysis_run.py`
- 创建：`backend/alembic/versions/20260611_match_material_decoupling.py`
- 修改：`backend/test/test_database_schema.py`

- [ ] **步骤 1：编写失败的 schema 测试**

在 `backend/test/test_database_schema.py` 的运行表结构测试中加入断言。若当前没有集中断言 `match_analysis_runs` 的测试，就在 `DatabaseSchemaTests` 中新增：

```python
    def test_match_analysis_runs_records_primary_material(self) -> None:
        columns = self._get_columns("match_analysis_runs")
        self.assertIn("primary_material_id", columns)

        indexes = {
            row[1]
            for row in self.connection.execute(
                "PRAGMA index_list('match_analysis_runs')",
            ).fetchall()
        }
        self.assertIn("ix_match_analysis_runs_primary_material_id", indexes)
```

在 `DatabaseSchemaTests` 的 helper 区域新增两个专用 helper，供迁移测试构造旧数据：

```python
    @staticmethod
    def _insert_identity_material_into(
        connection: sqlite3.Connection,
        identity_id: int,
        *,
        display_name: str = "简历",
        original_filename: str = "resume.txt",
        extracted_text: str = "resume",
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO identity_materials (
                identity_id,
                display_name,
                original_filename,
                file_path,
                material_type,
                sha256,
                extracted_text
            )
            VALUES (?, ?, ?, ?, 'resume', ?, ?)
            """,
            (
                identity_id,
                display_name,
                original_filename,
                f"data/materials/{original_filename}",
                "a" * 64,
                extracted_text,
            ),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _insert_email_task_with_material_into(
        connection: sqlite3.Connection,
        identity_id: int,
        llm_profile_id: int,
        professor_id: int,
        *,
        primary_material_id: int | None,
        updated_at: str = "2026-06-01 08:00:00",
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO email_tasks (
                source,
                identity_id,
                llm_profile_id,
                professor_id,
                primary_material_id,
                status,
                selected_material_ids,
                created_at,
                updated_at
            )
            VALUES ('manual', ?, ?, ?, ?, 'matched', ?, ?, ?)
            """,
            (
                identity_id,
                llm_profile_id,
                professor_id,
                primary_material_id,
                json.dumps([]),
                updated_at,
                updated_at,
            ),
        )
        return int(cursor.lastrowid)
```

在同文件的历史迁移测试区域新增一个迁移测试，构造旧库并验证回填：

```python
    def test_migration_backfills_match_run_primary_material_id(self) -> None:
        legacy_dir = tempfile.TemporaryDirectory()
        try:
            legacy_db_path = Path(legacy_dir.name) / "legacy_match_run_material.db"
            legacy_env = os.environ.copy()
            legacy_env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"
            self._run_alembic(legacy_env, "upgrade", "20260609rewrite")

            connection = sqlite3.connect(legacy_db_path)
            connection.execute("PRAGMA foreign_keys = ON")
            identity_id = self._insert_identity_into(connection, email_address="run-material@example.com")
            llm_profile_id = self._insert_llm_profile_into(connection, name="运行记录模型")
            professor_id = self._insert_professor_into(connection, "run-material@example.edu")
            material_id = self._insert_identity_material_into(
                connection,
                identity_id,
                original_filename="resume.txt",
                extracted_text="resume",
            )
            task_id = self._insert_email_task_with_material_into(
                connection,
                identity_id,
                llm_profile_id,
                professor_id,
                primary_material_id=material_id,
            )
            connection.execute(
                """
                INSERT INTO match_analysis_runs (
                    email_task_id, professor_id, identity_id, llm_profile_id,
                    status, success, match_score
                )
                VALUES (?, ?, ?, ?, 'succeeded', 1, 88)
                """,
                (task_id, professor_id, identity_id, llm_profile_id),
            )
            connection.commit()
            connection.close()

            self._run_alembic(legacy_env, "upgrade", "head")

            upgraded = sqlite3.connect(legacy_db_path)
            run_material_id = upgraded.execute(
                "SELECT primary_material_id FROM match_analysis_runs",
            ).fetchone()[0]
            self.assertEqual(run_material_id, material_id)
            upgraded.close()
        finally:
            legacy_dir.cleanup()
```

再新增一个默认材料补齐迁移测试：

```python
    def test_migration_recovers_identity_current_primary_material_from_recent_task(self) -> None:
        legacy_dir = tempfile.TemporaryDirectory()
        try:
            legacy_db_path = Path(legacy_dir.name) / "legacy_identity_primary_material.db"
            legacy_env = os.environ.copy()
            legacy_env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"
            self._run_alembic(legacy_env, "upgrade", "20260609rewrite")

            connection = sqlite3.connect(legacy_db_path)
            connection.execute("PRAGMA foreign_keys = ON")
            identity_id = self._insert_identity_into(connection, email_address="recover-primary@example.com")
            llm_profile_id = self._insert_llm_profile_into(connection, name="默认材料恢复模型")
            professor_id = self._insert_professor_into(connection, "recover-primary@example.edu")
            old_material_id = self._insert_identity_material_into(
                connection,
                identity_id,
                display_name="旧材料",
                original_filename="old-resume.txt",
                extracted_text="old",
            )
            recent_material_id = self._insert_identity_material_into(
                connection,
                identity_id,
                display_name="最近材料",
                original_filename="recent-resume.txt",
                extracted_text="recent",
            )
            self._insert_email_task_with_material_into(
                connection,
                identity_id,
                llm_profile_id,
                professor_id,
                primary_material_id=old_material_id,
                updated_at="2026-06-01 08:00:00",
            )
            self._insert_email_task_with_material_into(
                connection,
                identity_id,
                llm_profile_id,
                professor_id,
                primary_material_id=recent_material_id,
                updated_at="2026-06-02 08:00:00",
            )
            connection.commit()
            connection.close()

            self._run_alembic(legacy_env, "upgrade", "head")

            upgraded = sqlite3.connect(legacy_db_path)
            current_primary_material_id = upgraded.execute(
                "SELECT current_primary_material_id FROM identity_profiles WHERE id = ?",
                (identity_id,),
            ).fetchone()[0]
            self.assertEqual(current_primary_material_id, recent_material_id)
            upgraded.close()
        finally:
            legacy_dir.cleanup()
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && rtk uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_match_analysis_runs_records_primary_material
cd backend && rtk uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_migration_backfills_match_run_primary_material_id
cd backend && rtk uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_migration_recovers_identity_current_primary_material_from_recent_task
```

预期：第一个测试失败，提示 `primary_material_id` 或索引不存在；迁移测试失败，提示迁移后没有目标字段或默认材料没有补齐。

- [ ] **步骤 3：更新 SQLAlchemy 模型**

在 `backend/app/models/match_analysis_run.py` 中增加类型引用和字段：

```python
if TYPE_CHECKING:
    from app.models.identity_material import IdentityMaterial


class MatchAnalysisRun(Base):
    __tablename__ = "match_analysis_runs"

    primary_material_id: Mapped[int | None] = mapped_column(
        ForeignKey("identity_materials.id"),
        index=True,
        nullable=True,
    )

    primary_material: Mapped["IdentityMaterial | None"] = relationship()
```

保留已有 `email_task`、`professor`、`identity`、`llm_profile` relationships。

- [ ] **步骤 4：创建 Alembic migration**

创建 `backend/alembic/versions/20260611_match_material_decoupling.py`：

```python
"""decouple match analysis material from email task material

Revision ID: 20260611matchmat
Revises: 20260609rewrite
Create Date: 2026-06-11 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260611matchmat"
down_revision: Union[str, Sequence[str], None] = "20260609rewrite"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("match_analysis_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("primary_material_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_match_analysis_runs_primary_material_id_identity_materials",
            "identity_materials",
            ["primary_material_id"],
            ["id"],
        )
    op.create_index(
        "ix_match_analysis_runs_primary_material_id",
        "match_analysis_runs",
        ["primary_material_id"],
    )
    op.execute(
        """
        UPDATE match_analysis_runs
        SET primary_material_id = (
            SELECT email_tasks.primary_material_id
            FROM email_tasks
            WHERE email_tasks.id = match_analysis_runs.email_task_id
        )
        WHERE primary_material_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE identity_profiles
        SET current_primary_material_id = (
            SELECT identity_materials.id
            FROM identity_materials
            WHERE identity_materials.identity_id = identity_profiles.id
              AND (
                  lower(identity_materials.original_filename) LIKE '%.txt'
                  OR lower(identity_materials.original_filename) LIKE '%.md'
                  OR lower(identity_materials.original_filename) LIKE '%.pdf'
                  OR lower(identity_materials.original_filename) LIKE '%.docx'
              )
            ORDER BY identity_materials.created_at DESC, identity_materials.id DESC
            LIMIT 1
        )
        WHERE current_primary_material_id IS NULL
          AND (
              SELECT count(*)
              FROM identity_materials
              WHERE identity_materials.identity_id = identity_profiles.id
                AND (
                    lower(identity_materials.original_filename) LIKE '%.txt'
                    OR lower(identity_materials.original_filename) LIKE '%.md'
                    OR lower(identity_materials.original_filename) LIKE '%.pdf'
                    OR lower(identity_materials.original_filename) LIKE '%.docx'
                )
          ) = 1
        """
    )
    op.execute(
        """
        UPDATE identity_profiles
        SET current_primary_material_id = (
            SELECT email_tasks.primary_material_id
            FROM email_tasks
            JOIN identity_materials ON identity_materials.id = email_tasks.primary_material_id
            WHERE email_tasks.identity_id = identity_profiles.id
              AND identity_materials.identity_id = identity_profiles.id
              AND (
                  lower(identity_materials.original_filename) LIKE '%.txt'
                  OR lower(identity_materials.original_filename) LIKE '%.md'
                  OR lower(identity_materials.original_filename) LIKE '%.pdf'
                  OR lower(identity_materials.original_filename) LIKE '%.docx'
              )
            ORDER BY email_tasks.updated_at DESC, email_tasks.created_at DESC, email_tasks.id DESC
            LIMIT 1
        )
        WHERE current_primary_material_id IS NULL
          AND EXISTS (
              SELECT 1
              FROM email_tasks
              JOIN identity_materials ON identity_materials.id = email_tasks.primary_material_id
              WHERE email_tasks.identity_id = identity_profiles.id
                AND identity_materials.identity_id = identity_profiles.id
                AND (
                    lower(identity_materials.original_filename) LIKE '%.txt'
                    OR lower(identity_materials.original_filename) LIKE '%.md'
                    OR lower(identity_materials.original_filename) LIKE '%.pdf'
                    OR lower(identity_materials.original_filename) LIKE '%.docx'
                )
          )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_match_analysis_runs_primary_material_id", table_name="match_analysis_runs")
    with op.batch_alter_table("match_analysis_runs", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_match_analysis_runs_primary_material_id_identity_materials",
            type_="foreignkey",
        )
        batch_op.drop_column("primary_material_id")
```

迁移 SQL 只使用 SQLite 原生支持的 `lower()` 和 `LIKE`，不要使用 `reverse()` 这类 SQLite 未内置函数。

- [ ] **步骤 5：运行 migration 测试验证通过**

运行：

```bash
cd backend && rtk uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_match_analysis_runs_records_primary_material
cd backend && rtk uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_migration_backfills_match_run_primary_material_id
cd backend && rtk uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_migration_recovers_identity_current_primary_material_from_recent_task
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
rtk git add backend/app/models/match_analysis_run.py backend/alembic/versions/20260611_match_material_decoupling.py backend/test/test_database_schema.py
rtk git commit -m "feat(backend): 记录匹配分析使用材料"
```

---

### 任务 2：匹配 runtime 使用身份当前默认材料

**文件：**
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/test/test_match_analysis_runtime.py`
- 修改：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写失败的 runtime 测试**

在 `backend/test/test_match_analysis_runtime.py` 新增 helper，用于创建第二份材料并切换身份默认材料：

```python
    async def _switch_identity_primary_material(self, text: str = "新的默认材料") -> int:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, self.email_task_id)
            assert task is not None
            material = IdentityMaterial(
                identity_id=task.identity_id,
                display_name="新默认材料",
                file_path="data/materials/new-resume.txt",
                original_filename="new-resume.txt",
                material_type="resume",
                sha256="b" * 64,
                extracted_text=text,
            )
            session.add(material)
            await session.flush()
            identity = await session.get(IdentityProfile, task.identity_id)
            assert identity is not None
            identity.current_primary_material_id = material.id
            await session.commit()
            return material.id
```

新增测试：

```python
    def test_calculate_match_uses_identity_current_primary_material(self) -> None:
        new_material_id = self._run_async(
            self._switch_identity_primary_material("我现在主攻多智能体系统。"),
        )

        async def fake_generate_match_evaluation(**kwargs):
            self.assertEqual(kwargs["primary_material"].id, new_material_id)
            return llm_runtime.GeneratedMatchEvaluation(
                result=llm_runtime.MatchEvaluationResult(
                    match_score=89,
                    match_reason="使用新默认材料",
                    fit_points=["多智能体"],
                    risk_points=[],
                    keywords=["agent"],
                ),
                usage=None,
                endpoint_kind="chat_completions",
                status_code=200,
                duration_ms=10,
                prompt_hash="b" * 64,
                stable_prefix_hash="c" * 64,
            )

        with patch(
            "app.services.task_runtime.llm_runtime.generate_match_evaluation",
            AsyncMock(side_effect=fake_generate_match_evaluation),
        ):
            self._run_async(calculate_task_match_once(self.session_factory, self.email_task_id))

        runs = self._run_async(self._list_runs())
        self.assertEqual(runs[-1].primary_material_id, new_material_id)
```

新增任务材料清空后的回归测试：

```python
    def test_calculate_match_ignores_empty_task_primary_material_when_identity_has_primary(self) -> None:
        new_material_id = self._run_async(self._switch_identity_primary_material())
        self._run_async(self._clear_task_primary_material())

        with patch(
            "app.services.task_runtime.llm_runtime.generate_match_evaluation",
            AsyncMock(
                return_value=llm_runtime.GeneratedMatchEvaluation(
                    result=llm_runtime.MatchEvaluationResult(
                        match_score=77,
                        match_reason="仍可计算",
                        fit_points=[],
                        risk_points=[],
                        keywords=[],
                    ),
                    usage=None,
                    endpoint_kind="chat_completions",
                    status_code=200,
                    duration_ms=10,
                    prompt_hash="d" * 64,
                    stable_prefix_hash="e" * 64,
                ),
            ),
        ):
            self._run_async(calculate_task_match_once(self.session_factory, self.email_task_id))

        runs = self._run_async(self._list_runs())
        self.assertEqual(runs[-1].primary_material_id, new_material_id)
```

并添加 helper：

```python
    async def _clear_task_primary_material(self) -> None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, self.email_task_id)
            assert task is not None
            task.primary_material_id = None
            await session.commit()
```

将现有 `test_calculate_match_rejects_when_primary_material_has_no_extracted_text` 改为清空身份当前默认材料文本，而不是通过 `task.primary_material_id` 找材料。

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && rtk uv run python -m unittest \
  test.test_match_analysis_runtime.MatchAnalysisRuntimeTests.test_calculate_match_uses_identity_current_primary_material \
  test.test_match_analysis_runtime.MatchAnalysisRuntimeTests.test_calculate_match_ignores_empty_task_primary_material_when_identity_has_primary
```

预期：FAIL，当前实现仍使用旧 `task.primary_material` 或在任务材料为空时报“请先选择用于匹配的默认材料”。

- [ ] **步骤 3：实现身份默认材料解析 helper**

在 `backend/app/services/task_runtime.py` 中新增 helper：

```python
async def _resolve_match_primary_material(
    session: AsyncSession,
    task: EmailTask,
) -> IdentityMaterial:
    identity = task.identity
    primary_material = identity.current_primary_material
    if primary_material is None and identity.current_primary_material_id is not None:
        primary_material = await session.get(IdentityMaterial, identity.current_primary_material_id)
    if primary_material is None:
        raise ValueError("请到个人页设置默认材料")
    if primary_material.identity_id != task.identity_id:
        raise ValueError("个人页默认材料不属于当前身份")
    if not material_can_be_primary(primary_material):
        raise ValueError("个人页默认材料不支持匹配分析")
    return primary_material
```

确保 `_load_email_task()` 的 eager load 包含：

```python
selectinload(EmailTask.identity).selectinload(IdentityProfile.current_primary_material)
```

- [ ] **步骤 4：替换 `calculate_task_match()` 的材料读取**

把 `calculate_task_match()` 中：

```python
        if task.primary_material is None:
            if force:
                raise ValueError("请先选择用于匹配的默认材料")
            return _match_action_result(task)
        ensure_material_extracted_text(task.primary_material)
```

替换为：

```python
        try:
            match_primary_material = await _resolve_match_primary_material(session, task)
        except ValueError:
            if force:
                raise
            return _match_action_result(task)
        ensure_material_extracted_text(match_primary_material)
```

创建运行记录时传入材料：

```python
        run = await _create_running_match_analysis_run(
            session,
            task,
            primary_material=match_primary_material,
        )
```

LLM 调用传入：

```python
                primary_material=match_primary_material,
```

- [ ] **步骤 5：记录运行材料 id**

修改 `_create_running_match_analysis_run()` 签名：

```python
async def _create_running_match_analysis_run(
    session: AsyncSession,
    task: EmailTask,
    *,
    primary_material: IdentityMaterial,
) -> MatchAnalysisRun:
```

创建对象时加入：

```python
        primary_material_id=primary_material.id,
```

- [ ] **步骤 6：补 API 回归测试**

在 `backend/test/test_api_endpoints.py` 增加一个测试，复现用户反馈：

```python
    def test_calculate_match_after_switching_default_material_ignores_empty_task_material(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        old_material_id = self._upload_material(
            identity_id,
            filename="old-resume.txt",
            content=b"old resume",
            material_type="resume",
        )
        professor_id = self._create_professor(email="switch-match-material@example.edu")
        workspace = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        task_id = workspace.json()["current_task"]["id"]
        new_material_id = self._upload_material(
            identity_id,
            filename="new-resume.txt",
            content=b"new resume",
            material_type="resume",
        )
        self.client.post(f"/api/materials/{new_material_id}/set-primary")
        with self._connect() as connection:
            connection.execute(
                "UPDATE email_tasks SET primary_material_id = NULL WHERE id = ?",
                (task_id,),
            )
            connection.commit()

        async def fake_generate_match_evaluation(**kwargs):
            self.assertEqual(kwargs["primary_material"].id, new_material_id)
            return self._build_match_evaluation_result(match_score=92)

        with patch(
            "app.services.task_runtime.llm_runtime.generate_match_evaluation",
            AsyncMock(side_effect=fake_generate_match_evaluation),
        ):
            response = self.client.post(f"/api/email-tasks/{task_id}/calculate-match")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["thread"]["current_task"]["match_score"], 92)
```

- [ ] **步骤 7：运行后端匹配测试**

运行：

```bash
cd backend && rtk uv run python -m unittest test.test_match_analysis_runtime
cd backend && rtk uv run python -m unittest test.test_api_endpoints.EmailTaskApiTests.test_calculate_match_after_switching_default_material_ignores_empty_task_material
```

预期：PASS。

- [ ] **步骤 8：Commit**

```bash
rtk git add backend/app/services/task_runtime.py backend/test/test_match_analysis_runtime.py backend/test/test_api_endpoints.py
rtk git commit -m "fix(backend): 匹配分析使用身份当前默认材料"
```

---

### 任务 3：批量匹配执行时使用身份当前默认材料

**文件：**
- 修改：`backend/app/services/match_analysis_job_runtime.py`
- 修改：`backend/test/test_match_analysis_jobs.py`

- [ ] **步骤 1：编写失败的批量匹配测试**

在 `backend/test/test_match_analysis_jobs.py` 新增测试：

```python
    def test_job_execution_uses_identity_current_primary_material_not_existing_task_material(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(),
        )
        old_material_id = self._run_async(
            self._create_extra_material(identity_id, "old-resume.txt", "old"),
        )
        email_task_id = self._run_async(
            self._create_email_task(
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_id=professor_ids[0],
                primary_material_id=old_material_id,
            ),
        )
        new_material_id = self._run_async(
            self._switch_identity_primary_material(identity_id, "new-resume.txt", "new"),
        )
        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0]],
                name=None,
            ),
        )

        async def fake_generate_match_evaluation(**kwargs):
            self.assertEqual(kwargs["primary_material"].id, new_material_id)
            return self._build_match_evaluation_result(match_score=81)

        with patch(
            "app.services.task_runtime.llm_runtime.generate_match_evaluation",
            AsyncMock(side_effect=fake_generate_match_evaluation),
        ):
            self._run_async(run_queued_match_analysis_jobs_once(self.session_factory))

        items = self._run_async(self._get_job_items(job.id))
        self.assertEqual(items[0].email_task_id, email_task_id)
        self.assertEqual(items[0].status, MatchAnalysisJobItemStatus.SUCCEEDED.value)
```

把现有 `_create_email_task()` helper 扩展为可传 `primary_material_id`，并新增材料切换 helpers：

```python
    async def _create_email_task(
        self,
        *,
        identity_id: int,
        llm_profile_id: int,
        professor_id: int,
        primary_material_id: int | None = None,
    ) -> int:
        async with self.session_factory() as session:
            task = EmailTask(
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_id=professor_id,
                primary_material_id=primary_material_id,
                status=EmailTaskStatus.DISCOVERED.value,
            )
            session.add(task)
            await session.commit()
            return task.id

    async def _create_extra_material(self, identity_id: int, filename: str, text: str) -> int:
        async with self.session_factory() as session:
            material = IdentityMaterial(
                identity_id=identity_id,
                display_name=filename,
                original_filename=filename,
                file_path=f"data/uploads/{filename}",
                material_type=IdentityMaterialType.RESUME.value,
                sha256=filename.encode().hex().ljust(64, "0")[:64],
                extracted_text=text,
            )
            session.add(material)
            await session.commit()
            return material.id

    async def _switch_identity_primary_material(self, identity_id: int, filename: str, text: str) -> int:
        async with self.session_factory() as session:
            material = IdentityMaterial(
                identity_id=identity_id,
                display_name=filename,
                original_filename=filename,
                file_path=f"data/uploads/{filename}",
                material_type=IdentityMaterialType.RESUME.value,
                sha256=filename.encode().hex().ljust(64, "0")[:64],
                extracted_text=text,
            )
            session.add(material)
            await session.flush()
            identity = await session.get(IdentityProfile, identity_id)
            assert identity is not None
            identity.current_primary_material_id = material.id
            await session.commit()
            return material.id
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && rtk uv run python -m unittest test.test_match_analysis_jobs.MatchAnalysisJobRuntimeTests.test_job_execution_uses_identity_current_primary_material_not_existing_task_material
```

预期：FAIL，当前 `_ensure_match_email_task()` 可能补旧任务材料，或 runtime 使用旧任务材料。

- [ ] **步骤 3：调整 `_ensure_match_email_task()`**

在 `backend/app/services/match_analysis_job_runtime.py` 中删除这段匹配目的补写：

```python
        if existing_task.primary_material_id is None:
            existing_task.primary_material_id = identity.current_primary_material_id
```

新建任务时继续带入身份默认材料，作为后续 AI 写信参考材料；已存在任务不再被批量匹配流程补写或改写材料：

```python
    if existing_task is not None:
        return existing_task
```

- [ ] **步骤 4：运行批量匹配测试**

运行：

```bash
cd backend && rtk uv run python -m unittest test.test_match_analysis_jobs
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
rtk git add backend/app/services/match_analysis_job_runtime.py backend/test/test_match_analysis_jobs.py
rtk git commit -m "fix(backend): 批量匹配不复用任务材料"
```

---

### 任务 4：草稿生成和改写移除匹配上下文

**文件：**
- 修改：`backend/app/services/llm_runtime.py`
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/app/services/test_compose_runtime.py`
- 修改：`backend/test/test_llm_runtime.py`
- 修改：`backend/test/test_batch_draft_generation_runtime.py`
- 修改：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写失败的 prompt 测试**

在 `backend/test/test_llm_runtime.py` 新增测试：

```python
    def test_draft_prompt_does_not_include_match_context(self) -> None:
        identity = IdentityProfile(
            id=1,
            name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        material = IdentityMaterial(
            id=12,
            identity_id=1,
            display_name="简历",
            file_path="data/materials/resume.txt",
            original_filename="resume.txt",
            material_type="resume",
            extracted_text="我做过信息抽取与智能体相关研究。",
        )
        professor = Professor(
            id=5,
            name="李老师",
            email="prof@example.edu",
            title="Professor",
            university="Example University",
            school="Computer Science",
            department="AI",
            research_direction="Information Extraction",
            recent_papers=["Paper A"],
        )
        current_match = MatchEvaluationResult(
            match_score=99,
            match_reason="这个理由不应进入草稿",
            fit_points=["fit-secret"],
            risk_points=["risk-secret"],
            keywords=["keyword-secret"],
        )

        prompt = build_draft_prompt(
            identity=identity,
            primary_material=material,
            professor=professor,
            available_materials=[material],
            custom_subject="测试主题",
            custom_body="老师您好，我想申请。",
            custom_body_html=None,
            current_match=current_match,
        )

        self.assertNotIn("match_score", prompt)
        self.assertNotIn("这个理由不应进入草稿", prompt)
        self.assertNotIn("fit-secret", prompt)
        self.assertNotIn("risk-secret", prompt)
        self.assertNotIn("keyword-secret", prompt)
```

在改写 prompt 测试中新增：

```python
    def test_draft_rewrite_prompt_does_not_include_match_context(self) -> None:
        identity = IdentityProfile(
            id=1,
            name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        material = IdentityMaterial(
            id=12,
            identity_id=1,
            display_name="简历",
            file_path="data/materials/resume.txt",
            original_filename="resume.txt",
            material_type="resume",
            extracted_text="我做过信息抽取与智能体相关研究。",
        )
        professor = Professor(
            id=5,
            name="李老师",
            email="prof@example.edu",
            title="Professor",
            university="Example University",
            school="Computer Science",
            department="AI",
            research_direction="Information Extraction",
            recent_papers=["Paper A"],
        )
        document = build_draft_rewrite_document("<p>老师您好。</p>", {})
        parts = build_draft_rewrite_prompt_parts(
            identity=identity,
            primary_material=material,
            professor=professor,
            available_materials=[material],
            subject_template="测试主题",
            source_blocks=document.blocks,
            current_match=MatchEvaluationResult(
                match_score=98,
                match_reason="rewrite-secret",
                fit_points=["fit-secret"],
                risk_points=["risk-secret"],
                keywords=["keyword-secret"],
            ),
            rewrite_preferences=None,
            llm_profile=LLMProfile(
                id=7,
                provider="openai",
                api_base_url=None,
                api_key="test-key",
                model_name="gpt-test",
            ),
        )

        self.assertNotIn("current_match", parts.prompt)
        self.assertNotIn("rewrite-secret", parts.prompt)
        self.assertNotIn("fit-secret", parts.prompt)
        self.assertNotIn("risk-secret", parts.prompt)
        self.assertNotIn("keyword-secret", parts.prompt)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && rtk uv run python -m unittest \
  test.test_llm_runtime.LLMRuntimeTests.test_draft_prompt_does_not_include_match_context \
  test.test_llm_runtime.LLMRuntimeTests.test_draft_rewrite_prompt_does_not_include_match_context
```

预期：FAIL，当前 prompt 包含匹配上下文。

- [ ] **步骤 3：清理 task runtime 的 `current_match` 传递**

在 `backend/app/services/task_runtime.py` 中删除以下草稿路径的 `current_match = _build_match_result_from_task(task)`：

- 批量/任务草稿生成路径约 `generate_task_draft()`。
- 工作区 `rewrite_task_draft()`。
- 预览 `preview_task_draft()`。

调用 `llm_runtime.generate_draft_content()` 时移除：

```python
current_match=current_match,
```

在 `backend/app/services/test_compose_runtime.py` 中同样移除 `current_match=None` 参数。

- [ ] **步骤 4：清理 llm runtime 草稿参数**

在 `backend/app/services/llm_runtime.py` 中：

1. 从 `generate_draft_content()`、`estimate_draft_content_tokens()`、`build_draft_prompt()`、`_build_base_generation_prompt()`、`build_draft_rewrite_prompt()`、`build_draft_rewrite_prompt_parts()` 的签名中删除 `current_match`。
2. 删除 `match_context` 拼接。
3. 删除 `_build_base_generation_prompt()` 中 `payload["input"]["当前匹配"]`。
4. 删除 `build_draft_rewrite_prompt_parts()` 中 `prompt_input["current_match"]`。
5. 把 `extra_requirements` 的兜底文案从：

```python
{match_context or "当前还没有单独计算过匹配，请你自己综合判断邮件内容。"}
```

改成：

```python
{rewrite_preferences_block}
```

6. 保留“只生成邮件草稿，不要输出 match_score 等匹配字段”作为输出约束。

- [ ] **步骤 5：更新旧测试调用**

用 `rtk rg -n "current_match=" backend/test backend/app -S` 找到所有草稿路径调用，删除参数。匹配分析专用结构体测试保留 `MatchEvaluationResult`。

旧测试如断言 `current_match` 顺序或内容，改为断言 professor 信息仍在 prompt 中：

```python
self.assertIn("professor", parts.prompt)
self.assertNotIn("current_match", parts.prompt)
```

- [ ] **步骤 6：运行 LLM 和草稿相关测试**

运行：

```bash
cd backend && rtk uv run python -m unittest test.test_llm_runtime
cd backend && rtk uv run python -m unittest test.test_batch_draft_generation_runtime
cd backend && rtk uv run python -m unittest test.test_api_endpoints.EmailTaskApiTests
```

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
rtk git add backend/app/services/llm_runtime.py backend/app/services/task_runtime.py backend/app/services/test_compose_runtime.py backend/test/test_llm_runtime.py backend/test/test_batch_draft_generation_runtime.py backend/test/test_api_endpoints.py
rtk git commit -m "refactor(backend): 草稿生成脱离匹配结果"
```

---

### 任务 5：前端工作区匹配按钮和文案调整

**文件：**
- 修改：`frontend/src/pages/WorkspacePage.tsx`
- 修改：`frontend/src/components/organisms/WorkspaceComposerDock.tsx`
- 修改：`frontend/test/WorkspacePageNextStep.test.tsx`
- 修改：`frontend/src/pages/WorkspacePage.test.tsx`
- 修改：`frontend/test/WorkspaceComposerDockCopy.test.tsx`

- [ ] **步骤 1：编写失败的前端测试**

在 `frontend/test/WorkspacePageNextStep.test.tsx` 中新增场景，使用现有 `buildThread()`、`mockedUseSelectionContext` 和 `latestComposerDockProps()`：

```tsx
it("allows match analysis when task has no writing material but identity has current primary material", async () => {
  mockedGetWorkspaceThread.mockResolvedValue(
    buildThread({
      primaryMaterialId: null,
      professorResearchDirection: "NLP",
      professorRecentPapers: [],
    }),
  );
  mockedUseSelectionContext.mockReturnValue({
    selectedIdentityId: 1,
    selectedLlmProfileId: 1,
    selectedIdentity: {
      id: 1,
      name: "测试身份",
      profile_name: "测试身份",
      sender_name: "测试同学",
      email_address: "sender@example.com",
      current_primary_material_id: 11,
    },
    selectedLlmProfile: { id: 1, name: "测试模型" },
    loading: false,
  });

  renderPage();

  await waitFor(() => {
    expect(latestComposerDockProps().canCalculateMatch).toBe(true);
  });
});
```

在 `frontend/test/WorkspaceComposerDockCopy.test.tsx` 中新增文案断言，直接渲染现有 `WorkspaceComposerDock`：

```tsx
it("describes missing writing material as AI writing reference material", () => {
  render(
    <WorkspaceComposerDock
      {...baseProps}
      currentTask={{
        ...currentTask,
        primary_material: null,
        primary_material_id: null,
      }}
      draftReady={false}
      subject="测试主题"
      content="测试正文"
      contentHtml="<p>测试正文</p>"
      selectedMaterialIds={[]}
      scheduledAt=""
      acting={false}
      canChangeMode={true}
      canCalculateMatch={true}
      canGenerateDraft={false}
      canContinueManually={false}
      canStartFollowUp={false}
      canSubmitDraft={false}
      composerExpanded={true}
      nextStepTitle="AI 改写"
      nextStepDescription="基于当前编辑器内容生成个性化版本。"
    />,
  );

  expect(screen.getByText("请选择 AI 写信参考材料后再使用 AI 改写。")).toBeInTheDocument();
  expect(screen.queryByText("请选择用于匹配的材料。")).not.toBeInTheDocument();
});
```
      primary_material_id: null,
    },
  });

  expect(screen.getByText("请选择 AI 写信参考材料后再使用 AI 改写。")).toBeInTheDocument();
  expect(screen.queryByText("请选择用于匹配的材料。")).not.toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend && rtk npm run test -- WorkspacePageNextStep WorkspaceComposerDockCopy
```

预期：FAIL，当前 `canCalculateMatch` 依赖 `currentTask.primary_material_id`，文案仍然是“用于匹配的材料”。

- [ ] **步骤 3：调整 `WorkspacePage.tsx`**

在 `frontend/src/pages/WorkspacePage.tsx` 中把：

```tsx
  const canCalculateMatch =
    Boolean(currentTaskId) &&
    Boolean(currentTask?.primary_material_id) &&
    hasProfessorMatchEvidence(thread?.professor) &&
    !blocksDirectDraftActions;
```

改为：

```tsx
  const hasIdentityPrimaryMaterial = Boolean(
    selectedIdentity?.current_primary_material_id,
  );
  const canCalculateMatch =
    Boolean(currentTaskId) &&
    hasIdentityPrimaryMaterial &&
    hasProfessorMatchEvidence(thread?.professor) &&
    !blocksDirectDraftActions;
```

诊断 payload 中：

```tsx
hasPrimaryMaterial: Boolean(currentTask.primary_material_id),
```

改为：

```tsx
hasPrimaryMaterial: hasIdentityPrimaryMaterial,
```

保留 `canGenerateDraft` 对 `currentTask.primary_material_id` 的依赖，因为那是 AI 写信参考材料。

- [ ] **步骤 4：调整 `WorkspaceComposerDock.tsx` 文案**

把 limitation hint 中：

```tsx
: !currentTask.primary_material_id
  ? '请选择用于匹配的材料。'
```

改为：

```tsx
: !currentTask.primary_material_id
  ? '请选择 AI 写信参考材料后再使用 AI 改写。'
```

如果组件中还有“用于匹配的材料”字样，统一替换为“AI 写信参考材料”。

- [ ] **步骤 5：运行前端测试**

运行：

```bash
cd frontend && rtk npm run test -- WorkspacePageNextStep WorkspaceComposerDockCopy
cd frontend && rtk npm run lint
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
rtk git add frontend/src/pages/WorkspacePage.tsx frontend/src/components/organisms/WorkspaceComposerDock.tsx frontend/test/WorkspacePageNextStep.test.tsx frontend/src/pages/WorkspacePage.test.tsx frontend/test/WorkspaceComposerDockCopy.test.tsx
rtk git commit -m "fix(frontend): 匹配入口使用个人页默认材料"
```

---

### 任务 6：用户文档更新和最终验证

**文件：**
- 修改：`website/docs/matching.md`
- 修改：`website/docs/profile.md`
- 修改：`docs/material_management_design.md`
- 修改：`docs/database_table_design.md`

- [ ] **步骤 1：更新用户文档**

在 `website/docs/matching.md` 中把“你的主材料”说明改为：

```markdown
- 个人页当前默认材料
```

增加一句：

```markdown
匹配分析发起时会读取个人页当前默认材料；工作区里的“AI 写信参考材料”只影响草稿生成和改写。
```

在 `website/docs/profile.md` 的材料说明中补充：

```markdown
默认材料用于匹配分析；创建任务或工作区中选择的 AI 写信参考材料用于草稿生成。
```

- [ ] **步骤 2：更新开发文档**

在 `docs/material_management_design.md` 和 `docs/database_table_design.md` 中调整：

```markdown
- LLM 匹配读取 `identity_profiles.current_primary_material_id`。
- 草稿生成读取 `email_tasks.primary_material_id`，其语义为 AI 写信参考材料。
- `match_analysis_runs.primary_material_id` 记录每次匹配实际使用的默认材料。
```

保留“旧任务不会被动跟随个人页默认材料”的说明，但限定为草稿/写信参考材料，不再用于匹配分析。

- [ ] **步骤 3：运行后端重点验证**

运行：

```bash
cd backend && rtk uv run python -m unittest test.test_database_schema
cd backend && rtk uv run python -m unittest test.test_match_analysis_runtime
cd backend && rtk uv run python -m unittest test.test_match_analysis_jobs
cd backend && rtk uv run python -m unittest test.test_llm_runtime
```

预期：全部 PASS。

- [ ] **步骤 4：运行前端重点验证**

运行：

```bash
cd frontend && rtk npm run lint
cd frontend && rtk npm run test -- WorkspacePageNextStep WorkspaceComposerDockCopy HomePageMatchAnalysis
```

预期：全部 PASS。

- [ ] **步骤 5：运行文档或网站测试**

运行：

```bash
cd website && rtk npm run test
```

预期：PASS。

- [ ] **步骤 6：全局搜索残留语义**

运行：

```bash
rtk rg -n "用于匹配的材料|任务默认材料|current_match|当前匹配|match_score" backend/app/services/llm_runtime.py frontend/src docs website -S
```

预期：

- `llm_runtime.py` 的草稿路径不再出现 `current_match` 或 `当前匹配`。
- 文档中“任务默认材料”不再描述匹配分析材料来源。
- `match_score` 只出现在匹配分析、结果展示、禁止模型输出匹配字段等合理上下文。

- [ ] **步骤 7：Commit**

```bash
rtk git add website/docs/matching.md website/docs/profile.md docs/material_management_design.md docs/database_table_design.md
rtk git commit -m "docs: 更新匹配与写信材料语义"
```

---

## 最终验收清单

- [ ] `POST /api/email-tasks/{task_id}/calculate-match` 使用 `IdentityProfile.current_primary_material_id`。
- [ ] 旧 `EmailTask.primary_material_id` 为空但身份默认材料存在时，匹配分析成功。
- [ ] 切换个人页默认材料后重算匹配度使用新材料。
- [ ] `match_analysis_runs.primary_material_id` 记录本次匹配使用材料。
- [ ] 批量匹配执行时读取执行时身份默认材料。
- [ ] 草稿生成和改写 prompt 不包含匹配分、匹配理由、fit/risk/keywords。
- [ ] 工作区匹配按钮不依赖任务材料。
- [ ] 任务材料前端文案统一为“AI 写信参考材料”。
- [ ] 历史迁移不删除任务、草稿、匹配结果、发送记录或材料库。

## 推荐执行方式

优先使用 `superpowers:subagent-driven-development`：每个任务独立分派并审查，特别适合本计划中后端迁移、LLM prompt、前端文案三条相对独立的工作线。

如果在当前会话内执行，使用 `superpowers:executing-plans`，按任务顺序执行。每完成一个任务必须运行对应测试并 commit；不要把多个任务混成一个大提交。
