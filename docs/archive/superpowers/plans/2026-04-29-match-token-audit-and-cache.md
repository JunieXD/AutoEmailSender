# 匹配分析 Token 审计与缓存优化实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为“分析匹配度”增加 token 持久审计、全局消息汇总、受控并发、低随机性和 OpenAI prompt cache 命中优化。

**架构：** 新增 `match_analysis_runs` 作为每次匹配模型调用的审计表，`email_tasks` 继续保存最新匹配结果。后端匹配接口改为返回 `WorkspaceThreadRead + usage + run_id` 的包装结构；前端只适配匹配分析调用点，并把批量执行改为先 warm-up 再限流并发。匹配 prompt 拆出稳定前缀和动态后缀，OpenAI 官方 profile 才发送 `prompt_cache_key`。

**技术栈：** FastAPI、SQLAlchemy async ORM、Alembic、unittest、React 19、Vite、TypeScript、Vitest。

---

## 文件结构

- 创建 `backend/app/models/match_analysis_run.py`：SQLAlchemy 审计模型，只负责匹配分析运行记录。
- 修改 `backend/app/models/__init__.py`：导出 `MatchAnalysisRun`。
- 创建 `backend/alembic/versions/b8c9d0e1f2a3_add_match_analysis_runs.py`：创建审计表和索引。
- 修改 `backend/test/test_database_schema.py`：验证新表、字段、外键和 head revision。
- 修改 `backend/app/services/llm_runtime.py`：解析 cached tokens，构造稳定 prompt，控制匹配温度，注入 OpenAI prompt cache key，并把请求元数据传回调用者。
- 修改 `backend/test/test_llm_runtime.py`：覆盖 cached token 解析、prompt 稳定前缀、温度和 cache key。
- 修改 `backend/app/schemas/email_task.py`：新增匹配分析响应 DTO。
- 修改 `backend/app/api/email_tasks.py`：让 calculate-match 返回包装响应。
- 修改 `backend/app/services/task_runtime.py`：写入 `match_analysis_runs`，并返回本次 usage。
- 创建 `backend/test/test_match_analysis_runtime.py`：验证成功和失败审计写入、强制重算。
- 修改 `frontend/src/types/index.ts`：新增 usage 和 calculate-match 响应类型。
- 修改 `frontend/src/lib/api/emailTasksApi.ts`：更新 `calculateMatch` 返回类型。
- 创建 `frontend/src/features/match-analysis/client/tokenUsage.ts`：token 汇总、格式化和并发池 helper。
- 创建 `frontend/src/features/match-analysis/client/tokenUsage.test.ts`：测试 usage 汇总、缺失字段和并发上限。
- 修改 `frontend/src/pages/HomePage.tsx`：接入 usage 汇总通知和批量并发。

---

### 任务 1：新增匹配审计表

**文件：**
- 创建：`backend/app/models/match_analysis_run.py`
- 修改：`backend/app/models/__init__.py`
- 创建：`backend/alembic/versions/b8c9d0e1f2a3_add_match_analysis_runs.py`
- 测试：`backend/test/test_database_schema.py`

- [ ] **步骤 1：编写失败的数据库结构测试**

在 `backend/test/test_database_schema.py` 中把 head revision 改为新迁移 ID，并在 `test_runtime_tables_and_columns_are_created` 增加断言：

```python
HEAD_REVISION = "b8c9d0e1f2a3"

def test_runtime_tables_and_columns_are_created(self) -> None:
    rows = self.connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        """,
    ).fetchall()
    table_names = {row[0] for row in rows}

    self.assertIn("match_analysis_runs", table_names)
    match_run_columns = self._get_columns("match_analysis_runs")
    self.assertTrue(
        {
            "id",
            "email_task_id",
            "professor_id",
            "identity_id",
            "llm_profile_id",
            "success",
            "match_score",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cached_tokens",
            "duration_ms",
            "endpoint_kind",
            "status_code",
            "prompt_hash",
            "stable_prefix_hash",
            "error_message",
            "created_at",
        }.issubset(match_run_columns),
    )
```

新增索引检查：

```python
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
    }.issubset(match_run_indexes),
)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_runtime_tables_and_columns_are_created
```

预期：FAIL，报错包含 `match_analysis_runs` 不存在，或 Alembic 找不到 `b8c9d0e1f2a3`。

- [ ] **步骤 3：创建 SQLAlchemy 模型**

创建 `backend/app/models/match_analysis_run.py`：

```python
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.email_task import EmailTask
    from app.models.identity_profile import IdentityProfile
    from app.models.llm_profile import LLMProfile
    from app.models.professor import Professor


class MatchAnalysisRun(Base):
    __tablename__ = "match_analysis_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    email_task_id: Mapped[int] = mapped_column(
        ForeignKey("email_tasks.id"),
        index=True,
        nullable=False,
    )
    professor_id: Mapped[int] = mapped_column(
        ForeignKey("professors.id"),
        index=True,
        nullable=False,
    )
    identity_id: Mapped[int] = mapped_column(
        ForeignKey("identity_profiles.id"),
        index=True,
        nullable=False,
    )
    llm_profile_id: Mapped[int] = mapped_column(
        ForeignKey("llm_profiles.id"),
        index=True,
        nullable=False,
    )
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
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    email_task: Mapped["EmailTask"] = relationship()
    professor: Mapped["Professor"] = relationship()
    identity: Mapped["IdentityProfile"] = relationship()
    llm_profile: Mapped["LLMProfile"] = relationship()
```

修改 `backend/app/models/__init__.py`：

```python
from app.models.match_analysis_run import MatchAnalysisRun

__all__ = [
    # existing names...
    "MatchAnalysisRun",
]
```

- [ ] **步骤 4：创建 Alembic 迁移**

创建 `backend/alembic/versions/b8c9d0e1f2a3_add_match_analysis_runs.py`：

```python
"""add match analysis runs

Revision ID: b8c9d0e1f2a3
Revises: a4b6c8d0e2f1
Create Date: 2026-04-29 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a4b6c8d0e2f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "match_analysis_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email_task_id", sa.Integer(), nullable=False),
        sa.Column("professor_id", sa.Integer(), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column("llm_profile_id", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("match_score", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_tokens", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("endpoint_kind", sa.String(length=50), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("stable_prefix_hash", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["email_task_id"], ["email_tasks.id"]),
        sa.ForeignKeyConstraint(["professor_id"], ["professors.id"]),
        sa.ForeignKeyConstraint(["identity_id"], ["identity_profiles.id"]),
        sa.ForeignKeyConstraint(["llm_profile_id"], ["llm_profiles.id"]),
    )
    op.create_index("ix_match_analysis_runs_email_task_id", "match_analysis_runs", ["email_task_id"])
    op.create_index("ix_match_analysis_runs_professor_id", "match_analysis_runs", ["professor_id"])
    op.create_index("ix_match_analysis_runs_identity_id", "match_analysis_runs", ["identity_id"])
    op.create_index("ix_match_analysis_runs_llm_profile_id", "match_analysis_runs", ["llm_profile_id"])
    op.create_index("ix_match_analysis_runs_created_at", "match_analysis_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_match_analysis_runs_created_at", table_name="match_analysis_runs")
    op.drop_index("ix_match_analysis_runs_llm_profile_id", table_name="match_analysis_runs")
    op.drop_index("ix_match_analysis_runs_identity_id", table_name="match_analysis_runs")
    op.drop_index("ix_match_analysis_runs_professor_id", table_name="match_analysis_runs")
    op.drop_index("ix_match_analysis_runs_email_task_id", table_name="match_analysis_runs")
    op.drop_table("match_analysis_runs")
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_runtime_tables_and_columns_are_created
uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_old_revision_can_upgrade_to_head
```

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
git add backend/app/models backend/alembic/versions backend/test/test_database_schema.py
git commit -m "feat(匹配分析): 添加 token 审计表"
```

---

### 任务 2：扩展 LLM usage、prompt cache 和匹配确定性

**文件：**
- 修改：`backend/app/services/llm_runtime.py`
- 测试：`backend/test/test_llm_runtime.py`

- [ ] **步骤 1：编写失败的 usage 和 cache key 测试**

在 `backend/test/test_llm_runtime.py` 增加导入：

```python
from app.services.llm_runtime import (
    build_match_prompt_parts,
    generate_match_evaluation,
    parse_completion_usage,
)
```

新增测试：

```python
def test_parse_completion_usage_reads_cached_tokens_from_chat_shape(self) -> None:
    usage = parse_completion_usage(
        {
            "prompt_tokens": 1200,
            "completion_tokens": 80,
            "total_tokens": 1280,
            "prompt_tokens_details": {"cached_tokens": 1024},
        },
    )

    self.assertIsNotNone(usage)
    self.assertEqual(usage.prompt_tokens, 1200)
    self.assertEqual(usage.completion_tokens, 80)
    self.assertEqual(usage.total_tokens, 1280)
    self.assertEqual(usage.cached_tokens, 1024)


def test_build_match_prompt_parts_places_stable_identity_before_professor(self) -> None:
    from app.models import IdentityMaterial, IdentityProfile, Professor

    identity = IdentityProfile(
        id=3,
        name="张三",
        email_address="sender@example.com",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_username="sender@example.com",
        smtp_password="secret",
        default_language="zh-CN",
        outreach_generation_mode="llm",
    )
    primary_material = IdentityMaterial(
        id=7,
        identity_id=3,
        display_name="简历",
        file_path="data/materials/resume.txt",
        original_filename="resume.txt",
        material_type="resume",
        extracted_text="我做过信息抽取与智能体相关研究。",
    )
    professor = Professor(
        name="李老师",
        email="prof@example.edu",
        title="Professor",
        university="Example University",
        school="Computer Science",
        research_direction="Information Extraction",
        recent_papers=["Paper A"],
    )

    parts = build_match_prompt_parts(
        identity=identity,
        primary_material=primary_material,
        professor=professor,
        available_materials=[primary_material],
    )

    self.assertLess(parts.prompt.index("默认材料"), parts.prompt.index("导师信息"))
    self.assertIn("信息抽取与智能体", parts.stable_prefix)
    self.assertNotIn("李老师", parts.stable_prefix)
    self.assertEqual(len(parts.prompt_hash), 64)
    self.assertEqual(len(parts.stable_prefix_hash), 64)
```

新增异步测试，确认匹配分析固定 `temperature=0` 且官方 OpenAI profile 传 `prompt_cache_key`：

```python
async def test_generate_match_evaluation_uses_temperature_zero_and_prompt_cache_key(self) -> None:
    from app.models import IdentityMaterial, IdentityProfile, Professor

    identity = IdentityProfile(
        id=3,
        name="张三",
        email_address="sender@example.com",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_username="sender@example.com",
        smtp_password="secret",
        current_primary_material_id=7,
        default_language="zh-CN",
        outreach_generation_mode="llm",
    )
    primary_material = IdentityMaterial(
        id=7,
        identity_id=3,
        display_name="简历",
        file_path="data/materials/resume.txt",
        original_filename="resume.txt",
        material_type="resume",
        extracted_text="我做过信息抽取与智能体相关研究。",
    )
    profile = LLMProfile(
        id=5,
        name="openai",
        provider="openai",
        api_base_url=None,
        api_key="test-key",
        model_name="gpt-test",
        temperature=0.8,
    )
    professor = Professor(
        name="李老师",
        email="prof@example.edu",
        title="Professor",
        university="Example University",
        school="Computer Science",
        research_direction="Information Extraction",
        recent_papers=["Paper A"],
    )
    calls: list[tuple[str, dict[str, object] | None]] = []
    responses = [
        _FakeResponse(
            status_code=200,
            payload={
                "choices": [
                    {
                        "message": {
                            "content": '{"match_score":88,"match_reason":"方向匹配","fit_points":["信息抽取"],"risk_points":[],"keywords":["信息抽取"]}',
                        },
                    },
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                    "prompt_tokens_details": {"cached_tokens": 64},
                },
            },
        ),
    ]

    with patch(
        "app.services.llm_runtime.httpx.AsyncClient",
        side_effect=lambda *args, **kwargs: _FakeAsyncClient(responses, calls),
    ):
        result = await generate_match_evaluation(
            identity=identity,
            primary_material=primary_material,
            llm_profile=profile,
            professor=professor,
            available_materials=[primary_material],
        )

    payload = calls[0][1]
    self.assertEqual(payload["temperature"], 0)
    self.assertEqual(payload["prompt_cache_key"], "match:v1:3:7:5")
    self.assertEqual(result.usage.cached_tokens, 64)
    self.assertEqual(len(result.prompt_hash), 64)
    self.assertEqual(len(result.stable_prefix_hash), 64)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_parse_completion_usage_reads_cached_tokens_from_chat_shape
uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_build_match_prompt_parts_places_stable_identity_before_professor
uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_generate_match_evaluation_uses_temperature_zero_and_prompt_cache_key
```

预期：FAIL，原因包括 `cached_tokens`、`build_match_prompt_parts` 或 `prompt_cache_key` 尚不存在。

- [ ] **步骤 3：扩展 usage 与生成结果类型**

修改 `backend/app/services/llm_runtime.py`：

```python
import hashlib
```

扩展 dataclass：

```python
@dataclass(slots=True)
class ChatCompletionUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None


@dataclass(slots=True)
class MatchPromptParts:
    prompt: str
    stable_prefix: str
    prompt_hash: str
    stable_prefix_hash: str
    prompt_cache_key: str | None = None


@dataclass(slots=True)
class GeneratedMatchEvaluation:
    result: MatchEvaluationResult
    usage: ChatCompletionUsage | None = None
    request_url: str | None = None
    attempted_urls: list[str] = field(default_factory=list)
    endpoint_kind: str | None = None
    status_code: int | None = None
    duration_ms: int | None = None
    prompt_hash: str | None = None
    stable_prefix_hash: str | None = None
    prompt_cache_key: str | None = None
```

增加哈希 helper：

```python
def _hash_prompt(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
```

- [ ] **步骤 4：实现 cached token 解析**

替换 `parse_completion_usage`：

```python
def parse_completion_usage(raw_usage: object) -> ChatCompletionUsage | None:
    if not isinstance(raw_usage, dict):
        return None

    cached_tokens = None
    for details_key in ("prompt_tokens_details", "input_tokens_details"):
        details = raw_usage.get(details_key)
        if isinstance(details, dict):
            cached_tokens = _coerce_token_count(details.get("cached_tokens"))
            if cached_tokens is not None:
                break

    return ChatCompletionUsage(
        prompt_tokens=_coerce_token_count(
            raw_usage.get("prompt_tokens", raw_usage.get("input_tokens")),
        ),
        completion_tokens=_coerce_token_count(
            raw_usage.get("completion_tokens", raw_usage.get("output_tokens")),
        ),
        total_tokens=_coerce_token_count(raw_usage.get("total_tokens")),
        cached_tokens=cached_tokens,
    )
```

- [ ] **步骤 5：实现稳定 prompt parts 与官方 OpenAI 判断**

新增 helper：

```python
def _is_official_openai_profile(profile: LLMProfile) -> bool:
    if profile.provider != "openai":
        return False
    base_url = resolve_base_url(profile.api_base_url)
    return base_url.rstrip("/") == DEFAULT_BASE_URL


def _format_nullable(value: object) -> str:
    if value is None:
        return "未提供"
    if isinstance(value, str):
        return value.strip() or "未提供"
    return str(value)


def _build_match_prompt_cache_key(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    llm_profile: LLMProfile,
) -> str | None:
    if not _is_official_openai_profile(llm_profile):
        return None
    material_id = primary_material.id if primary_material is not None else "none"
    return f"match:v1:{identity.id}:{material_id}:{llm_profile.id}"
```

新增 `build_match_prompt_parts`，保留 `build_match_prompt` 作为兼容包装：

```python
def build_match_prompt_parts(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    llm_profile: LLMProfile | None = None,
) -> MatchPromptParts:
    sorted_materials = sorted(available_materials, key=lambda item: item.id or 0)
    material_lines = [
        f"- ID {material.id}: {material.display_name}（{material.material_type}）"
        for material in sorted_materials
    ]
    primary_text = (
        primary_material.extracted_text
        if primary_material is not None and primary_material.extracted_text
        else "未提供"
    )
    stable_prefix = dedent(
        f"""
        任务要求：
        1. 只判断匹配度，不要生成邮件草稿。
        2. match_reason 要简洁但具体。
        3. fit_points / risk_points / keywords 尽量聚焦，不要泛泛而谈。

        用户身份：
        - 姓名：{_format_nullable(identity.name)}
        - 邮箱：{_format_nullable(identity.email_address)}
        - 默认语言：{_format_nullable(identity.default_language)}
        - 匹配阈值：{_format_nullable(identity.match_threshold)}

        默认材料：
        {primary_text}

        可选材料：
        {chr(10).join(material_lines) if material_lines else "未提供"}
        """
    ).strip()
    dynamic_suffix = dedent(
        f"""
        导师信息：
        - 姓名：{_format_nullable(professor.name)}
        - 学校：{_format_nullable(professor.university)}
        - 学院：{_format_nullable(professor.school)}
        - 职称：{_format_nullable(professor.title)}
        - 研究方向：{_format_nullable(professor.research_direction)}
        - 近期论文：{json.dumps(professor.recent_papers or [], ensure_ascii=False)}
        """
    ).strip()
    prompt = f"{stable_prefix}\n\n{dynamic_suffix}"
    return MatchPromptParts(
        prompt=prompt,
        stable_prefix=stable_prefix,
        prompt_hash=_hash_prompt(prompt),
        stable_prefix_hash=_hash_prompt(stable_prefix),
        prompt_cache_key=(
            _build_match_prompt_cache_key(
                identity=identity,
                primary_material=primary_material,
                llm_profile=llm_profile,
            )
            if llm_profile is not None
            else None
        ),
    )


def build_match_prompt(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
) -> str:
    return build_match_prompt_parts(
        identity=identity,
        primary_material=primary_material,
        professor=professor,
        available_materials=available_materials,
    ).prompt
```

- [ ] **步骤 6：注入 prompt_cache_key 并固定匹配温度**

在 `generate_match_evaluation` 中使用 parts：

```python
parts = build_match_prompt_parts(
    identity=identity,
    primary_material=primary_material,
    professor=professor,
    available_materials=available_materials,
    llm_profile=llm_profile,
)
payload: dict[str, object] = {
    "model": llm_profile.model_name,
    "messages": [
        {"role": "system", "content": SYSTEM_MATCH_ONLY_PROMPT},
        {"role": "user", "content": parts.prompt},
    ],
    "temperature": 0,
    "max_tokens": llm_profile.max_tokens or DEFAULT_LLM_MAX_TOKENS,
}
if parts.prompt_cache_key:
    payload["prompt_cache_key"] = parts.prompt_cache_key
completion = await request_chat_completion(llm_profile, payload)
result = parse_structured_result(completion.content, MatchEvaluationResult)
return GeneratedMatchEvaluation(
    result=result,
    usage=completion.usage,
    request_url=completion.request_url,
    attempted_urls=completion.attempted_urls,
    endpoint_kind=completion.endpoint_kind,
    status_code=completion.status_code,
    duration_ms=completion.duration_ms,
    prompt_hash=parts.prompt_hash,
    stable_prefix_hash=parts.stable_prefix_hash,
    prompt_cache_key=parts.prompt_cache_key,
)
```

修改 `build_responses_payload`，透传 OpenAI 支持的缓存参数：

```python
if payload.get("prompt_cache_key") is not None:
    request_payload["prompt_cache_key"] = payload["prompt_cache_key"]
if payload.get("prompt_cache_retention") is not None:
    request_payload["prompt_cache_retention"] = payload["prompt_cache_retention"]
```

`request_chat_completion` 的 Chat Completions 分支直接使用原 `payload`，因此 `prompt_cache_key` 会随请求体发送。

- [ ] **步骤 7：运行 LLM runtime 测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_llm_runtime
```

预期：PASS。

- [ ] **步骤 8：Commit**

```powershell
git add backend/app/services/llm_runtime.py backend/test/test_llm_runtime.py
git commit -m "feat(匹配分析): 优化 prompt 缓存与 usage 解析"
```

---

### 任务 3：写入匹配审计记录并更新 API 响应

**文件：**
- 修改：`backend/app/schemas/email_task.py`
- 修改：`backend/app/api/email_tasks.py`
- 修改：`backend/app/services/task_runtime.py`
- 创建：`backend/test/test_match_analysis_runtime.py`

- [ ] **步骤 1：编写失败的运行时审计测试**

创建 `backend/test/test_match_analysis_runtime.py`：

```python
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Base,
    EmailTask,
    IdentityMaterial,
    IdentityProfile,
    LLMProfile,
    MatchAnalysisRun,
    Professor,
)
from app.services import llm_runtime
from app.services.task_runtime import calculate_task_match_once


class MatchAnalysisRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "match_analysis_test.db"
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.db_path.as_posix()}",
            future=True,
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )
        self._run_async(self._create_schema())
        self.email_task_id = self._run_async(self._create_email_task())

    def tearDown(self) -> None:
        self._run_async(self.engine.dispose())
        self.temp_dir.cleanup()

    def _run_async(self, awaitable):
        return asyncio.run(awaitable)

    async def _create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def _create_email_task(self) -> int:
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="测试身份",
                email_address="sender@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="llm",
            )
            profile = LLMProfile(
                name="测试模型",
                provider="openai",
                api_key="test-key",
                model_name="gpt-test",
            )
            professor = Professor(
                name="李老师",
                email="prof@example.edu",
                title="Professor",
                university="Example University",
                school="Computer Science",
                research_direction="Information Extraction",
                recent_papers=["Paper A"],
            )
            session.add_all([identity, profile, professor])
            await session.flush()

            material = IdentityMaterial(
                identity_id=identity.id,
                display_name="简历",
                file_path="data/materials/resume.txt",
                original_filename="resume.txt",
                material_type="resume",
                extracted_text="我做过信息抽取与智能体相关研究。",
            )
            session.add(material)
            await session.flush()
            identity.current_primary_material_id = material.id

            task = EmailTask(
                identity_id=identity.id,
                llm_profile_id=profile.id,
                professor_id=professor.id,
                primary_material_id=material.id,
                selected_material_ids=[],
            )
            session.add(task)
            await session.commit()
            return task.id

    def test_calculate_match_persists_successful_token_audit(self) -> None:
        generation = llm_runtime.GeneratedMatchEvaluation(
            result=llm_runtime.MatchEvaluationResult(
                match_score=91,
                match_reason="研究方向接近",
                fit_points=["信息抽取"],
                risk_points=[],
                keywords=["信息抽取"],
            ),
            usage=llm_runtime.ChatCompletionUsage(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                cached_tokens=64,
            ),
            endpoint_kind="chat_completions",
            status_code=200,
            duration_ms=321,
            prompt_hash="a" * 64,
            stable_prefix_hash="b" * 64,
        )

        with patch(
            "app.services.task_runtime.llm_runtime.generate_match_evaluation",
            new=AsyncMock(return_value=generation),
        ):
            result = self._run_async(
                calculate_task_match_once(
                    self.session_factory,
                    self.email_task_id,
                ),
            )

        self.assertEqual(result.usage.total_tokens, 120)
        self.assertIsNotNone(result.run_id)

        runs = self._run_async(self._list_runs())
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0].success)
        self.assertEqual(runs[0].match_score, 91)
        self.assertEqual(runs[0].cached_tokens, 64)

    def test_calculate_match_persists_failed_token_audit(self) -> None:
        with patch(
            "app.services.task_runtime.llm_runtime.generate_match_evaluation",
            new=AsyncMock(
                side_effect=llm_runtime.LLMRuntimeError(
                    "模型请求失败",
                    endpoint_kind="chat_completions",
                    status_code=500,
                    duration_ms=222,
                ),
            ),
        ):
            result = self._run_async(
                calculate_task_match_once(
                    self.session_factory,
                    self.email_task_id,
                ),
            )

        self.assertIsNone(result.usage.total_tokens)
        self.assertIsNotNone(result.run_id)

        runs = self._run_async(self._list_runs())
        self.assertEqual(len(runs), 1)
        self.assertFalse(runs[0].success)
        self.assertEqual(runs[0].status_code, 500)
        self.assertIn("模型请求失败", runs[0].error_message)

    async def _list_runs(self) -> list[MatchAnalysisRun]:
        async with self.session_factory() as session:
            return list(await session.scalars(select(MatchAnalysisRun)))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_match_analysis_runtime
```

预期：FAIL，原因包括 `MatchAnalysisRun`、`run_id` 或 response usage 还未实现。

- [ ] **步骤 3：新增响应 schema**

修改 `backend/app/schemas/email_task.py`：

```python
from app.schemas.workspace import WorkspaceThreadRead


class TokenUsageRead(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None


class MatchCalculationResultRead(BaseModel):
    thread: WorkspaceThreadRead
    usage: TokenUsageRead
    run_id: int | None = None
```

- [ ] **步骤 4：实现 task_runtime 返回对象和审计写入**

在 `backend/app/services/task_runtime.py` 增加导入：

```python
from dataclasses import dataclass

from app.models import MatchAnalysisRun
```

新增 dataclass：

```python
@dataclass(slots=True)
class MatchUsageSummary:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None


@dataclass(slots=True)
class MatchCalculationActionResult:
    professor_id: int
    identity_id: int
    llm_profile_id: int
    usage: MatchUsageSummary
    run_id: int | None = None
```

新增 helper：

```python
def _usage_summary(usage: llm_runtime.ChatCompletionUsage | None) -> MatchUsageSummary:
    if usage is None:
        return MatchUsageSummary()
    return MatchUsageSummary(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        cached_tokens=usage.cached_tokens,
    )
```

在 `calculate_task_match` 成功路径添加审计：

```python
run = MatchAnalysisRun(
    email_task_id=task.id,
    professor_id=task.professor_id,
    identity_id=task.identity_id,
    llm_profile_id=task.llm_profile_id,
    success=True,
    match_score=result.match_score,
    prompt_tokens=generation.usage.prompt_tokens if generation.usage else None,
    completion_tokens=generation.usage.completion_tokens if generation.usage else None,
    total_tokens=generation.usage.total_tokens if generation.usage else None,
    cached_tokens=generation.usage.cached_tokens if generation.usage else None,
    duration_ms=generation.duration_ms,
    endpoint_kind=generation.endpoint_kind,
    status_code=generation.status_code,
    prompt_hash=generation.prompt_hash,
    stable_prefix_hash=generation.stable_prefix_hash,
)
session.add(run)
await session.flush()
```

返回：

```python
return MatchCalculationActionResult(
    professor_id=task.professor_id,
    identity_id=task.identity_id,
    llm_profile_id=task.llm_profile_id,
    usage=_usage_summary(generation.usage),
    run_id=run.id,
)
```

在 `LLMRuntimeError` catch 中写失败记录：

```python
run = MatchAnalysisRun(
    email_task_id=task.id,
    professor_id=task.professor_id,
    identity_id=task.identity_id,
    llm_profile_id=task.llm_profile_id,
    success=False,
    duration_ms=exc.duration_ms,
    endpoint_kind=exc.endpoint_kind,
    status_code=exc.status_code,
    error_message=str(exc),
)
session.add(run)
await session.flush()
task.last_error = str(exc)
task.updated_at = datetime.now(UTC)
await session.commit()
return MatchCalculationActionResult(
    professor_id=task.professor_id,
    identity_id=task.identity_id,
    llm_profile_id=task.llm_profile_id,
    usage=MatchUsageSummary(),
    run_id=run.id,
)
```

前置校验早退也返回 `MatchCalculationActionResult(..., usage=MatchUsageSummary())`，但不写 run。

- [ ] **步骤 5：更新 API 端点返回包装响应**

修改 `backend/app/api/email_tasks.py`：

```python
from app.schemas.email_task import (
    EmailTaskApprovalRequest,
    EmailTaskOutreachConfigRequest,
    EmailTaskPrimaryMaterialRequest,
    EmailTaskScheduleRequest,
    MatchCalculationResultRead,
    TokenUsageRead,
)
```

把 calculate-match endpoint 改为：

```python
@router.post("/{task_id}/calculate-match", response_model=MatchCalculationResultRead)
async def calculate_match(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> MatchCalculationResultRead:
    try:
        result = await calculate_task_match_once(get_session_factory(), task_id)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "不存在" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc

    thread = await build_workspace_thread(
        session,
        professor_id=result.professor_id,
        identity_id=result.identity_id,
        llm_profile_id=result.llm_profile_id,
    )
    return MatchCalculationResultRead(
        thread=thread,
        usage=TokenUsageRead(
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            total_tokens=result.usage.total_tokens,
            cached_tokens=result.usage.cached_tokens,
        ),
        run_id=result.run_id,
    )
```

其他 endpoint 继续使用 `_run_workspace_action`。

- [ ] **步骤 6：运行后端匹配审计测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_match_analysis_runtime
uv run python -m unittest test.test_llm_runtime
uv run python -m unittest test.test_database_schema
```

预期：PASS。

- [ ] **步骤 7：Commit**

```powershell
git add backend/app/schemas/email_task.py backend/app/api/email_tasks.py backend/app/services/task_runtime.py backend/test/test_match_analysis_runtime.py
git commit -m "feat(匹配分析): 持久保存 token 审计记录"
```

---

### 任务 4：前端 usage 类型、汇总 helper 与并发池

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/lib/api/emailTasksApi.ts`
- 创建：`frontend/src/features/match-analysis/client/tokenUsage.ts`
- 创建：`frontend/src/features/match-analysis/client/tokenUsage.test.ts`

- [ ] **步骤 1：编写失败的前端 helper 测试**

创建 `frontend/src/features/match-analysis/client/tokenUsage.test.ts`：

```typescript
import { describe, expect, it } from 'vitest';
import {
  formatTokenUsageDescription,
  runWithConcurrency,
  sumTokenUsage,
  type TokenUsage,
} from './tokenUsage';

describe('tokenUsage', () => {
  it('sums nullable usage fields', () => {
    const usage = sumTokenUsage([
      { prompt_tokens: 10, completion_tokens: 2, total_tokens: 12, cached_tokens: 4 },
      { prompt_tokens: null, completion_tokens: 3, total_tokens: 3, cached_tokens: null },
    ]);

    expect(usage).toEqual({
      prompt_tokens: 10,
      completion_tokens: 5,
      total_tokens: 15,
      cached_tokens: 4,
    });
  });

  it('formats missing fields as not returned', () => {
    const description = formatTokenUsageDescription({
      prompt_tokens: null,
      completion_tokens: 5,
      total_tokens: null,
      cached_tokens: 2,
    });

    expect(description).toBe('输入 未返回 / 输出 5 / 总计 未返回 / 缓存命中 2');
  });

  it('limits concurrency', async () => {
    let active = 0;
    let maxActive = 0;
    const items = [1, 2, 3, 4, 5];

    const results = await runWithConcurrency(items, 2, async (item) => {
      active += 1;
      maxActive = Math.max(maxActive, active);
      await new Promise((resolve) => setTimeout(resolve, 1));
      active -= 1;
      return item * 2;
    });

    expect(results).toEqual([2, 4, 6, 8, 10]);
    expect(maxActive).toBeLessThanOrEqual(2);
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd frontend
npm run test -- src/features/match-analysis/client/tokenUsage.test.ts
```

预期：FAIL，模块不存在。

- [ ] **步骤 3：新增前端类型**

修改 `frontend/src/types/index.ts`：

```typescript
export interface TokenUsageDTO {
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  cached_tokens: number | null;
}

export interface MatchCalculationResultDTO {
  thread: WorkspaceThreadDTO;
  usage: TokenUsageDTO;
  run_id: number | null;
}
```

修改 `frontend/src/lib/api/emailTasksApi.ts`：

```typescript
import type {
  EmailTaskApprovalPayloadDTO,
  EmailTaskOutreachConfigPayloadDTO,
  EmailTaskSchedulePayloadDTO,
  MatchCalculationResultDTO,
  WorkspaceThreadDTO,
} from '@/types';

export const calculateMatch = (taskId: number) =>
  apiFetch<MatchCalculationResultDTO>(`/api/email-tasks/${taskId}/calculate-match`, {
    method: 'POST',
  });
```

- [ ] **步骤 4：实现 token usage helper**

创建 `frontend/src/features/match-analysis/client/tokenUsage.ts`：

```typescript
export type TokenUsage = {
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  cached_tokens: number | null;
};

const emptyUsage = (): TokenUsage => ({
  prompt_tokens: null,
  completion_tokens: null,
  total_tokens: null,
  cached_tokens: null,
});

const addNullable = (left: number | null, right: number | null): number | null => {
  if (left === null && right === null) return null;
  return (left ?? 0) + (right ?? 0);
};

export const sumTokenUsage = (items: TokenUsage[]): TokenUsage =>
  items.reduce((total, item) => ({
    prompt_tokens: addNullable(total.prompt_tokens, item.prompt_tokens),
    completion_tokens: addNullable(total.completion_tokens, item.completion_tokens),
    total_tokens: addNullable(total.total_tokens, item.total_tokens),
    cached_tokens: addNullable(total.cached_tokens, item.cached_tokens),
  }), emptyUsage());

const formatTokenValue = (value: number | null): string =>
  value === null ? '未返回' : value.toLocaleString('zh-CN');

export const formatTokenUsageDescription = (usage: TokenUsage): string =>
  `输入 ${formatTokenValue(usage.prompt_tokens)} / 输出 ${formatTokenValue(
    usage.completion_tokens,
  )} / 总计 ${formatTokenValue(usage.total_tokens)} / 缓存命中 ${formatTokenValue(
    usage.cached_tokens,
  )}`;

export async function runWithConcurrency<T, R>(
  items: T[],
  concurrency: number,
  worker: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let nextIndex = 0;
  const workerCount = Math.min(Math.max(concurrency, 1), items.length);

  await Promise.all(
    Array.from({ length: workerCount }, async () => {
      while (nextIndex < items.length) {
        const currentIndex = nextIndex;
        nextIndex += 1;
        results[currentIndex] = await worker(items[currentIndex], currentIndex);
      }
    }),
  );

  return results;
}
```

- [ ] **步骤 5：运行前端 helper 测试验证通过**

运行：

```powershell
cd frontend
npm run test -- src/features/match-analysis/client/tokenUsage.test.ts
```

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
git add frontend/src/types/index.ts frontend/src/lib/api/emailTasksApi.ts frontend/src/features/match-analysis
git commit -m "feat(frontend): 添加匹配 token 汇总工具"
```

---

### 任务 5：接入导师看板通知与批量并发

**文件：**
- 修改：`frontend/src/pages/HomePage.tsx`
- 测试：`frontend/src/features/match-analysis/client/tokenUsage.test.ts`

- [ ] **步骤 1：编写失败的 warm-up 顺序测试**

扩展 `frontend/src/features/match-analysis/client/tokenUsage.ts`，先在测试中声明将要新增的 helper：

```typescript
import { runWarmupThenConcurrent } from './tokenUsage';

it('runs first item before concurrent remainder', async () => {
  const events: string[] = [];
  const results = await runWarmupThenConcurrent([1, 2, 3], 2, async (item) => {
    events.push(`start:${item}`);
    await new Promise((resolve) => setTimeout(resolve, item === 1 ? 2 : 1));
    events.push(`end:${item}`);
    return item;
  });

  expect(results).toEqual([1, 2, 3]);
  expect(events.slice(0, 2)).toEqual(['start:1', 'end:1']);
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd frontend
npm run test -- src/features/match-analysis/client/tokenUsage.test.ts
```

预期：FAIL，`runWarmupThenConcurrent` 不存在。

- [ ] **步骤 3：实现 warm-up helper**

在 `frontend/src/features/match-analysis/client/tokenUsage.ts` 增加：

```typescript
export async function runWarmupThenConcurrent<T, R>(
  items: T[],
  concurrency: number,
  worker: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  if (items.length === 0) return [];

  const first = await worker(items[0], 0);
  const rest = await runWithConcurrency(items.slice(1), concurrency, (item, index) =>
    worker(item, index + 1),
  );
  return [first, ...rest];
}
```

- [ ] **步骤 4：修改 HomePage 单个分析通知**

在 `frontend/src/pages/HomePage.tsx` 增加导入：

```typescript
import {
  formatTokenUsageDescription,
  runWarmupThenConcurrent,
  sumTokenUsage,
  type TokenUsage,
} from '@/features/match-analysis/client/tokenUsage';
```

确保通知 hook 包含成功通知：

```typescript
const { notifyError, notifySuccess, notifyWarning } = useNotification();
```

修改 `runCalculateMatchForProfessor` 返回 usage：

```typescript
const runCalculateMatchForProfessor = useCallback(
  async (professorId: number): Promise<TokenUsage> => {
    if (!selectedIdentityId || !selectedLlmProfileId) {
      throw new Error('请先选择身份和模型');
    }

    const workspace = await ensureWorkspaceTask(professorId, selectedIdentityId, selectedLlmProfileId);
    if (!workspace.current_task.id) {
      throw new Error('未能为该导师准备工作区任务');
    }
    const result = await calculateMatch(workspace.current_task.id);
    return result.usage;
  },
  [selectedIdentityId, selectedLlmProfileId],
);
```

修改单个成功路径：

```typescript
const usage = await runCalculateMatchForProfessor(professorId);
await loadProfessors();
notifySuccess('匹配分析完成', formatTokenUsageDescription(usage));
```

- [ ] **步骤 5：修改批量分析为 warm-up + 并发汇总**

在 `handleGenerateSelected` 中替换串行 `for`：

```typescript
const failedNames: string[] = [];
const successfulUsages: TokenUsage[] = [];

try {
  await runWarmupThenConcurrent(analyzableProfessors, 3, async (professor) => {
    toggleScoringProfessor(professor.id, true);
    try {
      const usage = await runCalculateMatchForProfessor(professor.id);
      successfulUsages.push(usage);
      return usage;
    } catch (actionError) {
      failedNames.push(
        actionError instanceof Error
          ? `${professor.name}：${actionError.message}`
          : `${professor.name}：计算匹配失败`,
      );
      return null;
    } finally {
      toggleScoringProfessor(professor.id, false);
    }
  });
  await loadProfessors();

  const usage = sumTokenUsage(successfulUsages);
  const summary = `成功 ${successfulUsages.length} 位 / 失败 ${failedNames.length} 位 / 跳过 ${skippedCount} 位；${formatTokenUsageDescription(usage)}`;

  if (failedNames.length > 0) {
    notifyError('部分导师计算失败', `${summary}；${failedNames.slice(0, 2).join('；')}`);
  } else {
    notifySuccess('批量匹配分析完成', summary);
  }
} finally {
  setBulkScoring(false);
}
```

保持“跳过缺少研究信息”的 warning 可以保留，但最终 token 结果只发一条成功/失败汇总消息。若产品希望严格只有一条通知，移除前置 skipped warning，只在最终 summary 展示 skipped。

- [ ] **步骤 6：运行前端测试和 lint/build**

运行：

```powershell
cd frontend
npm run test -- src/features/match-analysis/client/tokenUsage.test.ts
npm run lint
npm run build
```

预期：全部 PASS。

- [ ] **步骤 7：Commit**

```powershell
git add frontend/src/pages/HomePage.tsx frontend/src/features/match-analysis/client/tokenUsage.ts frontend/src/features/match-analysis/client/tokenUsage.test.ts
git commit -m "feat(frontend): 汇总显示匹配 token 并发分析"
```

---

### 任务 6：全量验证与收尾

**文件：**
- 修改：按前序任务产生的全部文件

- [ ] **步骤 1：运行后端重点测试**

运行：

```powershell
cd backend
uv run python -m unittest test.test_database_schema test.test_llm_runtime test.test_match_analysis_runtime test.test_workspace_support
```

预期：PASS。

- [ ] **步骤 2：运行前端验证**

运行：

```powershell
cd frontend
npm run test
npm run lint
npm run build
```

预期：PASS。

- [ ] **步骤 3：手动检查 API 类型影响**

运行：

```powershell
Get-ChildItem -Path frontend/src -Recurse -File | Select-String -Pattern 'calculateMatch\\('
```

预期：只看到 `HomePage.tsx` 和 `WorkspacePage.tsx` 等调用点；如果 `WorkspacePage.tsx` 仍期望 `WorkspaceThreadDTO`，需要更新它读取 `result.thread`，或新增一个 API helper 保持旧行为。不要让 TypeScript build 带着类型错误通过。

- [ ] **步骤 4：手动冒烟验证**

启动后端：

```powershell
cd backend
uv run uvicorn main:app --reload
```

启动前端：

```powershell
cd frontend
npm run dev
```

在浏览器中验证：

- 单个导师点击“分析匹配度”，成功后全局消息显示 token。
- 再次点击同一导师，会重新分析并新增一条 `match_analysis_runs` 记录。
- 多选至少 4 位导师批量分析，行级 loading 同时最多 3 个，最终只显示一条汇总 token 消息。
- 后端数据库 `match_analysis_runs` 中存在对应成功记录；OpenAI 官方模型返回 usage 时 `cached_tokens` 可被保存。

- [ ] **步骤 5：最终状态检查**

运行：

```powershell
git status --short
git log -5 --oneline
```

预期：只存在本功能相关改动；所有计划内提交都在最近提交中。

- [ ] **步骤 6：最终 Commit 或确认无需提交**

如果还有验证修复：

```powershell
git add backend frontend
git commit -m "fix(匹配分析): 完善 token 审计验证"
```

如果没有额外改动，记录“无需额外提交”。

---

## 自检映射

- 持久保存 token 审计：任务 1、任务 3。
- 单个和批量全局消息显示 token：任务 4、任务 5。
- 批量受控并发：任务 4、任务 5。
- 已有匹配强制重算：任务 3 测试和 `calculate_task_match_once` 保持 `force=True`。
- 降低随机性：任务 2 固定匹配 `temperature=0`。
- Prompt cache 命中优化：任务 2 稳定前缀、OpenAI `prompt_cache_key` 和 cached token 解析。
- 不保存完整 prompt：任务 1 只设计 hash 字段，任务 3 只写 hash。
- 不影响草稿生成：任务 2 只修改 `generate_match_evaluation` 的温度和 prompt parts。
