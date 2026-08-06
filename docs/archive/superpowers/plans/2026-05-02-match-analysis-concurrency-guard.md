# 匹配分析并发防重入实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为匹配分析增加后端防重入、运行状态审计和前端 `409` 反馈，避免同一 `email_task` 被并行重复分析。

**架构：** 扩展 `match_analysis_runs`，把运行状态从单一 `success` 布尔值升级为 `running / succeeded / failed` 三态；后端在通过前置校验后先创建一条 `running` 记录，并依赖数据库唯一约束阻止同一任务重复进入运行态；前端保留现有 warm-up + 限流并发，只在收到 `409` 时给出专门提示。

**技术栈：** FastAPI、SQLAlchemy async ORM、Alembic、SQLite、unittest、React 19、Vite、Vitest。

---

## 文件结构

- 修改 `backend/app/models/match_analysis_run.py`：为匹配分析运行记录补充状态字段和唯一约束。
- 修改 `backend/app/models/__init__.py`：如果模型导出列表未自动覆盖，确认 `MatchAnalysisRun` 继续可导入。
- 创建 `backend/alembic/versions/d4c3b2a190ef_add_match_analysis_run_lock_fields.py`：为 `match_analysis_runs` 增加状态字段、时间字段、错误类型字段和部分唯一索引。
- 修改 `backend/test/test_database_schema.py`：验证新字段和运行中唯一索引。
- 修改 `backend/app/services/task_runtime.py`：创建运行中记录、捕获唯一约束冲突、更新运行结果。
- 修改 `backend/app/api/email_tasks.py`：把运行中冲突映射成 `409 Conflict`。
- 修改 `backend/test/test_match_analysis_runtime.py`：覆盖运行中冲突和状态落库。
- 修改 `backend/test/test_api_endpoints.py`：覆盖 `POST /api/email-tasks/{task_id}/calculate-match` 返回 `409`。
- 创建 `frontend/test/HomePageMatchAnalysis.test.tsx`：覆盖单次匹配遇到 `409` 时的提示，以及批量匹配继续执行其他导师。
- 修改 `frontend/src/pages/HomePage.tsx`：识别 `ApiError.status === 409`，显示更准确的用户提示。

---

### 任务 1：扩展匹配运行表并加运行中唯一锁

**文件：**
- 修改：`backend/test/test_database_schema.py`
- 修改：`backend/app/models/match_analysis_run.py`
- 创建：`backend/alembic/versions/d4c3b2a190ef_add_match_analysis_run_lock_fields.py`

- [ ] **步骤 1：先补失败的数据库结构测试**

在 `backend/test/test_database_schema.py` 的匹配运行表断言中加入新列和新索引：

```python
        self.assertTrue(
            {
                "status",
                "started_at",
                "finished_at",
                "error_kind",
            }.issubset(match_run_columns),
        )

        match_run_indexes = {
            row[1]
            for row in self.connection.execute(
                "PRAGMA index_list('match_analysis_runs')"
            ).fetchall()
        }
        self.assertTrue(
            {
                "ix_match_analysis_runs_email_task_id",
                "ix_match_analysis_runs_professor_id",
                "ix_match_analysis_runs_created_at",
                "uq_match_analysis_runs_running_per_task",
            }.issubset(match_run_indexes),
        )
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_runtime_tables_and_columns_are_created
```

预期：FAIL，报错包含 `status`、`started_at`、`finished_at` 或 `uq_match_analysis_runs_running_per_task` 不存在。

- [ ] **步骤 3：更新 SQLAlchemy 模型**

把 `backend/app/models/match_analysis_run.py` 调整为：

```python
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text


class MatchAnalysisRun(Base):
    __tablename__ = "match_analysis_runs"
    __table_args__ = (
        Index(
            "uq_match_analysis_runs_running_per_task",
            "email_task_id",
            unique=True,
            sqlite_where=text("status = 'running'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email_task_id: Mapped[int] = mapped_column(ForeignKey("email_tasks.id"), index=True, nullable=False)
    professor_id: Mapped[int] = mapped_column(ForeignKey("professors.id"), index=True, nullable=False)
    identity_id: Mapped[int] = mapped_column(ForeignKey("identity_profiles.id"), index=True, nullable=False)
    llm_profile_id: Mapped[int] = mapped_column(ForeignKey("llm_profiles.id"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'failed'"))
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    match_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    endpoint_kind: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stable_prefix_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
```

- [ ] **步骤 4：编写迁移**

创建 `backend/alembic/versions/d4c3b2a190ef_add_match_analysis_run_lock_fields.py`：

```python
"""add match analysis run lock fields

Revision ID: d4c3b2a190ef
Revises: b8c9d0e1f2a3
Create Date: 2026-05-02 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4c3b2a190ef"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("match_analysis_runs") as batch_op:
        batch_op.add_column(sa.Column("status", sa.String(length=32), server_default=sa.text("'failed'"), nullable=False))
        batch_op.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("error_kind", sa.String(length=32), nullable=True))

    op.execute("UPDATE match_analysis_runs SET status = CASE WHEN success = 1 THEN 'succeeded' ELSE 'failed' END")
    op.create_index(
        "uq_match_analysis_runs_running_per_task",
        "match_analysis_runs",
        ["email_task_id"],
        unique=True,
        sqlite_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("uq_match_analysis_runs_running_per_task", table_name="match_analysis_runs")
    with op.batch_alter_table("match_analysis_runs") as batch_op:
        batch_op.drop_column("error_kind")
        batch_op.drop_column("finished_at")
        batch_op.drop_column("started_at")
        batch_op.drop_column("status")
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_runtime_tables_and_columns_are_created
```

预期：PASS，新列和唯一索引都存在。

- [ ] **步骤 6：Commit**

```powershell
rtk git add backend/app/models/match_analysis_run.py backend/alembic/versions/d4c3b2a190ef_add_match_analysis_run_lock_fields.py backend/test/test_database_schema.py
rtk git commit -m "feat(匹配分析): 为运行记录增加防重入字段"
```

### 任务 2：后端在前置校验后创建运行中记录并返回 409

**文件：**
- 修改：`backend/test/test_match_analysis_runtime.py`
- 修改：`backend/test/test_api_endpoints.py`
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/app/api/email_tasks.py`

- [ ] **步骤 1：先补失败的运行时测试**

在 `backend/test/test_match_analysis_runtime.py` 中新增运行中冲突测试：

```python
    def test_calculate_match_rejects_when_another_run_is_running(self) -> None:
        self._run_async(self._insert_running_run())

        with self.assertRaisesRegex(RuntimeError, "该任务正在分析中"):
            self._run_async(calculate_task_match_once(self.session_factory, self.email_task_id))

    async def _insert_running_run(self) -> None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, self.email_task_id)
            assert task is not None
            session.add(
                MatchAnalysisRun(
                    email_task_id=task.id,
                    professor_id=task.professor_id,
                    identity_id=task.identity_id,
                    llm_profile_id=task.llm_profile_id,
                    status="running",
                    success=False,
                )
            )
            await session.commit()
```

在 `backend/test/test_api_endpoints.py` 中新增接口测试：

```python
    def test_calculate_match_returns_409_when_run_is_already_running(self) -> None:
        task_id = self._create_email_task_for_match()

        with patch(
            "app.api.email_tasks.calculate_task_match_once",
            side_effect=RuntimeError("该任务正在分析中"),
        ):
            response = self.client.post(f"/api/email-tasks/{task_id}/calculate-match")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "该任务正在分析中")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_match_analysis_runtime.MatchAnalysisRuntimeTests.test_calculate_match_rejects_when_another_run_is_running
rtk uv run python -m unittest test.test_api_endpoints.EmailTaskApiTests.test_calculate_match_returns_409_when_run_is_already_running
```

预期：FAIL，当前实现不会检测运行中冲突，接口仍返回 `200` 或 `400`。

- [ ] **步骤 3：在运行时增加防重入异常和运行中记录**

在 `backend/app/services/task_runtime.py` 中加入：

```python
class MatchAnalysisAlreadyRunningError(RuntimeError):
    pass


async def _create_running_match_analysis_run(session: AsyncSession, task: EmailTask) -> MatchAnalysisRun:
    run = MatchAnalysisRun(
        email_task_id=task.id,
        professor_id=task.professor_id,
        identity_id=task.identity_id,
        llm_profile_id=task.llm_profile_id,
        status="running",
        success=False,
        started_at=datetime.now(UTC),
    )
    session.add(run)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise MatchAnalysisAlreadyRunningError("该任务正在分析中") from exc
    return run
```

然后把 `calculate_task_match(...)` 的主流程改成：

```python
        ensure_material_extracted_text(task.primary_material)
        if not _has_professor_match_evidence(task.professor):
            raise ValueError("缺少研究方向或近期论文，暂不能分析匹配度")

        run = await _create_running_match_analysis_run(session, task)

        try:
            generation = await llm_runtime.generate_match_evaluation(...)
        except llm_runtime.LLMRuntimeError as exc:
            run.status = "failed"
            run.error_kind = "llm_runtime"
            run.error_message = str(exc)
            run.duration_ms = exc.duration_ms
            run.endpoint_kind = exc.endpoint_kind
            run.status_code = exc.status_code
            run.finished_at = datetime.now(UTC)
            await session.commit()
            return _match_action_result(task, run_id=run.id)

        run.status = "succeeded"
        run.success = True
        run.match_score = generation.result.match_score
        run.prompt_tokens = generation.usage.prompt_tokens if generation.usage else None
        run.completion_tokens = generation.usage.completion_tokens if generation.usage else None
        run.total_tokens = generation.usage.total_tokens if generation.usage else None
        run.cached_tokens = generation.usage.cached_tokens if generation.usage else None
        run.finished_at = datetime.now(UTC)
```

- [ ] **步骤 4：把 API 冲突映射成 409**

在 `backend/app/api/email_tasks.py` 中调整异常分支：

```python
    except MatchAnalysisAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "不存在" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test.test_match_analysis_runtime
rtk uv run python -m unittest test.test_api_endpoints.EmailTaskApiTests.test_calculate_match_returns_409_when_run_is_already_running
```

预期：PASS，运行中冲突会抛出专门异常，API 返回 `409`。

- [ ] **步骤 6：Commit**

```powershell
rtk git add backend/app/services/task_runtime.py backend/app/api/email_tasks.py backend/test/test_match_analysis_runtime.py backend/test/test_api_endpoints.py
rtk git commit -m "feat(匹配分析): 增加运行中防重入校验"
```

### 任务 3：前端把 409 显示为“正在分析中”，并保留批量继续执行

**文件：**
- 创建：`frontend/test/HomePageMatchAnalysis.test.tsx`
- 修改：`frontend/src/pages/HomePage.tsx`

- [ ] **步骤 1：先补失败的前端测试**

创建 `frontend/test/HomePageMatchAnalysis.test.tsx`，写两个测试：

```typescript
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";
import { HomePage } from "@/pages/HomePage";

const mockedNotifyError = vi.hoisted(() => vi.fn());
const mockedNotifyWarning = vi.hoisted(() => vi.fn());
const mockedCalculateMatch = vi.hoisted(() => vi.fn());
const mockedEnsureWorkspaceTask = vi.hoisted(() => vi.fn());

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => ({
    notifyError: mockedNotifyError,
    notifySuccess: vi.fn(),
    notifyWarning: mockedNotifyWarning,
  }),
}));

vi.mock("@/lib/api/emailTasksApi", () => ({
  calculateMatch: mockedCalculateMatch,
}));

vi.mock("@/lib/api/workspacesApi", () => ({
  ensureWorkspaceTask: mockedEnsureWorkspaceTask,
}));

it("shows a warning when a single match request returns 409", async () => {
  mockedCalculateMatch.mockRejectedValue(new ApiError(409, "该任务正在分析中"));
  render(
    <MemoryRouter>
      <HomePage />
    </MemoryRouter>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "分析匹配度" }));

  await waitFor(() => {
    expect(mockedNotifyWarning).toHaveBeenCalledWith("匹配分析进行中", "该任务正在分析中，请稍后刷新结果。");
  });
});

it("continues batch scoring after one 409 conflict", async () => {
  mockedCalculateMatch
    .mockRejectedValueOnce(new ApiError(409, "该任务正在分析中"))
    .mockResolvedValueOnce({
      thread: {} as never,
      usage: { prompt_tokens: 10, completion_tokens: 2, total_tokens: 12, cached_tokens: 0 },
      run_id: 1,
    });

  render(
    <MemoryRouter>
      <HomePage />
    </MemoryRouter>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "批量分析匹配度" }));

  await waitFor(() => {
    expect(mockedNotifyError).toHaveBeenCalledWith(
      "部分导师计算失败",
      expect.stringContaining("正在分析中"),
    );
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk npm --prefix frontend run test -- HomePageMatchAnalysis.test.tsx
```

预期：FAIL，当前实现会把 `409` 当作普通错误处理，不会显示专门提示。

- [ ] **步骤 3：在 HomePage 中区分 409**

在 `frontend/src/pages/HomePage.tsx` 中加入：

```typescript
import { ApiError } from "@/lib/api/client";

const isMatchConflictError = (error: unknown) =>
  error instanceof ApiError && error.status === 409;
```

然后修改单次匹配错误分支：

```typescript
    } catch (actionError) {
      if (isMatchConflictError(actionError)) {
        notifyWarning("匹配分析进行中", "该任务正在分析中，请稍后刷新结果。");
        return;
      }
      const message = actionError instanceof Error ? actionError.message : "计算匹配失败";
      notifyError("计算匹配失败", message);
    } finally {
```

批量逻辑中保留继续执行，只调整冲突文本：

```typescript
            failedNames.push(
              isMatchConflictError(actionError)
                ? `${professor.name}：正在分析中`
                : actionError instanceof Error
                  ? `${professor.name}：${actionError.message}`
                  : `${professor.name}：计算匹配失败`,
            );
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
rtk npm --prefix frontend run test -- HomePageMatchAnalysis.test.tsx
```

预期：PASS，单次匹配显示警告，批量匹配仍继续跑完并汇总。

- [ ] **步骤 5：Commit**

```powershell
rtk git add frontend/src/pages/HomePage.tsx frontend/test/HomePageMatchAnalysis.test.tsx
rtk git commit -m "feat(前端): 处理匹配分析运行中冲突提示"
```

## 自检

- 规格覆盖度：已覆盖匹配运行表状态字段、后端防重入、`409` API 语义和前端冲突提示。
- 占位符扫描：计划中没有 `TODO`、`待定`、`后续实现` 之类的占位符；所有步骤都给出了文件、代码或命令。
- 类型一致性：计划统一使用 `MatchAnalysisAlreadyRunningError`、`status=running/succeeded/failed` 和 `ApiError.status === 409`。

## 执行交接

计划已完成并保存到 `docs/superpowers/plans/2026-05-02-match-analysis-concurrency-guard.md`。两种执行方式：

**1. 子代理驱动（推荐）** - 每个任务调度一个新的子代理，任务间进行审查，快速迭代

**2. 内联执行** - 在当前会话中使用 executing-plans 执行任务，批量执行并设有检查点

选哪种方式？
