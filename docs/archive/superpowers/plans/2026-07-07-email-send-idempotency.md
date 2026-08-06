# 发信幂等与中断恢复实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为真实发信链路增加跨前端重试、后端重启、SMTP 中断和后台恢复的幂等保护，确保同一用户意图不会重复投递同一封邮件。

**架构：** 新增 `EmailSendAttempt` 记录真实 SMTP 尝试的持久阶段，新增 `IdempotencyRecord` 记录请求级幂等结果；`dispatch_email_task` 在 SMTP 前落库 attempt 和 Message-ID，SMTP 成功后先写 `sent` 与 `EmailLog`，已发送箱同步改成后置收尾。恢复 worker 对已进入 SMTP 风险区的任务只做系统核验，不自动恢复为可发送状态。

**技术栈：** FastAPI、SQLAlchemy 2.x、Alembic、SQLite、Python unittest、React、TypeScript、Vitest。

---

## 规格来源

- 规格文档：`docs/superpowers/specs/2026-07-07-email-send-idempotency-design.md`
- 当前 Alembic head：`20260703_imap_folder_history_scan`
- 当前核心路径：
  - `backend/app/services/task_runtime.py`
  - `backend/app/services/mail_runtime.py`
  - `backend/app/api/email_tasks.py`
  - `backend/app/api/batch_tasks.py`
  - `frontend/src/lib/api/client.ts`
  - `frontend/src/lib/api/emailTasksApi.ts`
  - `frontend/src/lib/api/batchTasksApi.ts`

## 文件结构

- 创建：`backend/app/models/email_send_attempt.py`
  - 定义 `EmailSendAttemptPhase` 和 `EmailSendAttempt`。
  - 保存一次真实发送尝试的 Message-ID、阶段、租约、SMTP 时间点、核验证据和错误摘要。

- 创建：`backend/app/models/idempotency_record.py`
  - 定义 `IdempotencyRecordStatus` 和 `IdempotencyRecord`。
  - 保存同一个 `Idempotency-Key` 对应的请求指纹、执行状态、响应快照和副作用边界。

- 修改：`backend/app/models/email_task.py`
  - 新增状态 `send_confirming`、`send_unconfirmed`。
  - 新增 `send_generation`，用于服务端后台发送 key 稳定生成。
  - 增加 `email_send_attempts` relationship。

- 修改：`backend/app/models/email_log.py`
  - 增加 `send_attempt_id`、`send_attempt_key`、`idempotency_key`，让发送日志能反查 attempt。

- 修改：`backend/app/models/__init__.py`
  - 导出新增模型与枚举。

- 创建：`backend/alembic/versions/20260707_email_send_idempotency.py`
  - 从 `20260703_imap_folder_history_scan` 接上迁移。
  - 创建 `email_send_attempts`、`idempotency_records`。
  - 给 `email_tasks` 增加 `send_generation`。
  - 给 `email_logs` 增加 attempt 关联字段和索引。

- 创建：`backend/app/services/idempotency.py`
  - 负责请求指纹、幂等记录领取、完成、冲突、恢复中状态判断。

- 创建：`backend/app/services/email_send_attempts.py`
  - 负责 attempt key、Message-ID 预生成、阶段更新、租约接管、sent 持久化辅助。

- 创建：`backend/app/services/email_send_verification.py`
  - 负责中断发送的系统核验，优先用 attempt、本地 `EmailLog`、已发送箱 Message-ID 和内容指纹证据。

- 修改：`backend/app/services/mail_runtime.py`
  - 支持调用方传入稳定 `rfc_message_id`。
  - 支持 `sync_sent_folder=False`，让 `task_runtime` 在 SMTP 成功后先落库。
  - 新增后置已发送箱同步和 Message-ID 查询函数。

- 修改：`backend/app/services/task_runtime.py`
  - `dispatch_email_task` 改为 attempt 驱动。
  - `recover_stale_sending_tasks` 改为基于 attempt 阶段恢复。
  - 状态门禁禁止 `sending/send_confirming/send_unconfirmed/sent/reply_detected/canceled` 被审核入口回写为可发送。

- 修改：`backend/app/api/email_tasks.py`
  - 读取 `Idempotency-Key`，传入 `approve_and_send_task`。
  - 对重复 key 返回第一次结果或当前恢复状态。

- 修改：`backend/app/api/batch_tasks.py`
  - `create_batch_task` 支持请求幂等。
  - 批量 item 的 `approve-and-send` 支持请求幂等。

- 修改：`backend/app/services/batch_task_item_actions.py`
  - `send_confirming/send_unconfirmed` 不进入可重发队列。

- 修改：`backend/app/services/batch_task_resend_context.py`
  - `send_confirming/send_unconfirmed` 在批量重发上下文中默认不可选。

- 修改：`backend/app/schemas/batch_task.py`
  - 增加批量卡片和 item 所需的确认中、未确认计数。

- 修改：`frontend/src/lib/api/idempotency.ts`
  - 新增前端幂等 key 生成和 header 合并工具。

- 修改：`frontend/src/lib/api/client.ts`
  - 桌面端网络重试只允许安全方法，或带 `Idempotency-Key` 的写方法。

- 修改：`frontend/src/lib/api/emailTasksApi.ts`
  - `approveAndSend` 发送 `Idempotency-Key`。

- 修改：`frontend/src/lib/api/batchTasksApi.ts`
  - `createBatchTask`、`approveAndSendBatchTaskItemDraft` 发送 `Idempotency-Key`。

- 修改：`frontend/src/types/index.ts`
  - 增加 `send_confirming`、`send_unconfirmed` 状态类型和中文文案。

- 修改测试：
  - `backend/test/test_email_send_idempotency_models.py`
  - `backend/test/test_idempotency_service.py`
  - `backend/test/test_email_send_attempts.py`
  - `backend/test/test_email_send_recovery.py`
  - `backend/test/test_batch_task_dispatch_schedule.py`
  - `backend/test/test_concurrency_guards.py`
  - `backend/test/test_database_schema.py`
  - `backend/test/test_mail_runtime.py`
  - `backend/test/test_api_endpoints.py`
  - `frontend/test/apiClient.test.ts`
  - `frontend/test/BatchTasksApi.test.ts`
  - `frontend/test/EmailTasksApi.test.ts`
  - `frontend/src/features/workspace/client/getWorkspaceNextStep.test.ts`

## 关键约定

- `Idempotency-Key` header 名固定为 `Idempotency-Key`。
- 请求指纹使用 `method + normalized_path + canonical_json_body` 的 SHA-256。
- 前端每次用户点击生成一个新 key；同一次 `apiFetch` 自动重试复用同一个 key。
- 后台 dispatcher 没有前端 key，服务端生成：`auto-send:{email_task_id}:{send_generation}`。
- `send_generation` 初始为 `1`。当前实现不自动递增它；显式重新发送功能接入时才递增。
- `EmailSendAttempt.attempt_key` 固定为 `email-task:{email_task_id}:generation:{send_generation}`。
- `prepared` 阶段表示尚未进入 SMTP，可以安全接管。
- `smtp_inflight` 及之后表示已经进入真实 SMTP 风险区，不能自动重发。
- `send_confirming` 和 `send_unconfirmed` 都不在 dispatcher 可领取状态内。

---

### 任务 1：数据模型、迁移和 schema 验证

**文件：**
- 创建：`backend/app/models/email_send_attempt.py`
- 创建：`backend/app/models/idempotency_record.py`
- 修改：`backend/app/models/email_task.py`
- 修改：`backend/app/models/email_log.py`
- 修改：`backend/app/models/__init__.py`
- 创建：`backend/alembic/versions/20260707_email_send_idempotency.py`
- 创建：`backend/test/test_email_send_idempotency_models.py`
- 修改：`backend/test/test_database_schema.py`

- [ ] **步骤 1：编写失败的模型测试**

创建 `backend/test/test_email_send_idempotency_models.py`：

```python
from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Base,
    EmailSendAttempt,
    EmailSendAttemptPhase,
    EmailTask,
    EmailTaskStatus,
    IdempotencyRecord,
    IdempotencyRecordStatus,
)


class EmailSendIdempotencyModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self._run_async(self._create_schema())

    def tearDown(self) -> None:
        self._run_async(self.engine.dispose())

    def _run_async(self, awaitable):
        return asyncio.run(awaitable)

    async def _create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    def test_email_task_has_new_confirmation_statuses_and_generation(self) -> None:
        self.assertEqual(EmailTaskStatus.SEND_CONFIRMING.value, "send_confirming")
        self.assertEqual(EmailTaskStatus.SEND_UNCONFIRMED.value, "send_unconfirmed")

        async def scenario() -> int:
            async with self.session_factory() as session:
                task = EmailTask(identity_id=1, llm_profile_id=1, professor_id=1)
                session.add(task)
                await session.commit()
                saved = await session.get(EmailTask, task.id)
                assert saved is not None
                return saved.send_generation

        self.assertEqual(self._run_async(scenario()), 1)

    def test_email_send_attempt_persists_phase_and_message_id(self) -> None:
        now = datetime(2026, 7, 7, 10, 0, tzinfo=UTC)

        async def scenario() -> tuple[str, str, str]:
            async with self.session_factory() as session:
                attempt = EmailSendAttempt(
                    email_task_id=42,
                    attempt_key="email-task:42:generation:1",
                    idempotency_key="auto-send:42:1",
                    rfc_message_id="<attempt@example.com>",
                    identity_id=7,
                    professor_id=9,
                    recipient_email="teacher@example.edu",
                    subject="Hello",
                    body_fingerprint="sha256:body",
                    attachment_fingerprint="sha256:attachments",
                    phase=EmailSendAttemptPhase.PREPARED.value,
                    lease_owner="worker-1",
                    lease_expires_at=now + timedelta(minutes=5),
                    heartbeat_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(attempt)
                await session.commit()
                saved = await session.scalar(select(EmailSendAttempt))
                assert saved is not None
                return saved.attempt_key, saved.rfc_message_id, saved.phase

        self.assertEqual(
            self._run_async(scenario()),
            (
                "email-task:42:generation:1",
                "<attempt@example.com>",
                EmailSendAttemptPhase.PREPARED.value,
            ),
        )

    def test_idempotency_record_rejects_duplicate_scope_key(self) -> None:
        now = datetime(2026, 7, 7, 10, 0, tzinfo=UTC)

        async def scenario() -> None:
            async with self.session_factory() as session:
                session.add_all(
                    [
                        IdempotencyRecord(
                            scope="POST /api/email-tasks/1/approve-and-send",
                            key="same-key",
                            request_fingerprint="sha256:first",
                            status=IdempotencyRecordStatus.IN_PROGRESS.value,
                            lease_owner="worker-1",
                            lease_expires_at=now + timedelta(minutes=5),
                            created_at=now,
                            updated_at=now,
                        ),
                        IdempotencyRecord(
                            scope="POST /api/email-tasks/1/approve-and-send",
                            key="same-key",
                            request_fingerprint="sha256:first",
                            status=IdempotencyRecordStatus.IN_PROGRESS.value,
                            lease_owner="worker-2",
                            lease_expires_at=now + timedelta(minutes=5),
                            created_at=now,
                            updated_at=now,
                        ),
                    ],
                )
                await session.commit()

        with self.assertRaises(IntegrityError):
            self._run_async(scenario())
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_email_send_idempotency_models -v
```

预期：
`ImportError` 或 `AttributeError`，因为 `EmailSendAttempt`、`IdempotencyRecord`、新状态和 `send_generation` 尚不存在。

- [ ] **步骤 3：实现模型**

创建 `backend/app/models/email_send_attempt.py`：

```python
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.email_task import EmailTask


class EmailSendAttemptPhase(StrEnum):
    PREPARED = "prepared"
    SMTP_INFLIGHT = "smtp_inflight"
    SMTP_ACCEPTED = "smtp_accepted"
    PERSISTED_SENT = "persisted_sent"
    VERIFYING_SENT_FOLDER = "verifying_sent_folder"
    COMPLETED = "completed"
    FAILED_SAFE = "failed_safe"
    CONFIRMING = "confirming"
    UNCONFIRMED = "unconfirmed"


class EmailSendAttempt(Base):
    __tablename__ = "email_send_attempts"
    __table_args__ = (
        UniqueConstraint("attempt_key", name="uq_email_send_attempts_attempt_key"),
        UniqueConstraint(
            "email_task_id",
            "idempotency_key",
            name="uq_email_send_attempts_task_idempotency_key",
        ),
        Index("ix_email_send_attempts_email_task_id", "email_task_id"),
        Index("ix_email_send_attempts_phase", "phase"),
        Index("ix_email_send_attempts_lease_expires_at", "lease_expires_at"),
        Index("ix_email_send_attempts_rfc_message_id", "rfc_message_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email_task_id: Mapped[int] = mapped_column(ForeignKey("email_tasks.id"), nullable=False)
    attempt_key: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    rfc_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    identity_id: Mapped[int] = mapped_column(ForeignKey("identity_profiles.id"), nullable=False)
    professor_id: Mapped[int] = mapped_column(ForeignKey("professors.id"), nullable=False)
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_fingerprint: Mapped[str] = mapped_column(String(80), nullable=False)
    attachment_fingerprint: Mapped[str] = mapped_column(String(80), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    smtp_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    smtp_accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    verification_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    verification_finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    verification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    verification_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    verification_evidence: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    provider_payload: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    email_task: Mapped["EmailTask"] = relationship(back_populates="email_send_attempts")
```

创建 `backend/app/models/idempotency_record.py`：

```python
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime


class IdempotencyRecordStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED_BEFORE_SIDE_EFFECT = "failed_before_side_effect"
    SIDE_EFFECT_STARTED = "side_effect_started"
    RECOVERING = "recovering"
    COMPLETED_UNKNOWN = "completed_unknown"


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("scope", "key", name="uq_idempotency_records_scope_key"),
        Index("ix_idempotency_records_key", "key"),
        Index("ix_idempotency_records_status", "status"),
        Index("ix_idempotency_records_lease_expires_at", "lease_expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(255), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    side_effect_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    result_entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
```

修改 `backend/app/models/email_task.py`：

```python
class EmailTaskStatus(StrEnum):
    DISCOVERED = "discovered"
    MATCHED = "matched"
    GENERATING_DRAFT = "generating_draft"
    DRAFT_FAILED = "draft_failed"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    SENDING = "sending"
    SEND_CONFIRMING = "send_confirming"
    SEND_UNCONFIRMED = "send_unconfirmed"
    SENT = "sent"
    SEND_FAILED = "send_failed"
    REPLY_DETECTED = "reply_detected"
    CANCELED = "canceled"
```

在 `EmailTask` 字段区加入：

```python
    send_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
```

在 `TYPE_CHECKING` 区加入：

```python
    from app.models.email_send_attempt import EmailSendAttempt
```

在 relationships 区加入：

```python
    email_send_attempts: Mapped[list["EmailSendAttempt"]] = relationship(
        back_populates="email_task",
        cascade="all, delete-orphan",
    )
```

修改 `backend/app/models/email_log.py`，在字段区加入：

```python
    send_attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_send_attempts.id"),
        index=True,
        nullable=True,
    )
    send_attempt_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

修改 `backend/app/models/__init__.py`，导入并导出：

```python
from app.models.email_send_attempt import EmailSendAttempt, EmailSendAttemptPhase
from app.models.idempotency_record import IdempotencyRecord, IdempotencyRecordStatus
```

- [ ] **步骤 4：创建 Alembic 迁移**

创建 `backend/alembic/versions/20260707_email_send_idempotency.py`：

```python
"""add email send idempotency

Revision ID: 20260707_send_idempotency
Revises: 20260703_imap_folder_history_scan
Create Date: 2026-07-07 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260707_send_idempotency"
down_revision: Union[str, Sequence[str], None] = "20260703_imap_folder_history_scan"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "email_tasks",
        sa.Column("send_generation", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.create_table(
        "email_send_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email_task_id", sa.Integer(), nullable=False),
        sa.Column("attempt_key", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("rfc_message_id", sa.String(length=255), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column("professor_id", sa.Integer(), nullable=False),
        sa.Column("recipient_email", sa.String(length=255), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body_fingerprint", sa.String(length=80), nullable=False),
        sa.Column("attachment_fingerprint", sa.String(length=80), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("smtp_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("smtp_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_confidence", sa.Float(), nullable=True),
        sa.Column("verification_attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("verification_evidence", sa.JSON(), nullable=True),
        sa.Column("provider_payload", sa.JSON(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["email_task_id"], ["email_tasks.id"], name=op.f("fk_email_send_attempts_email_task_id_email_tasks")),
        sa.ForeignKeyConstraint(["identity_id"], ["identity_profiles.id"], name=op.f("fk_email_send_attempts_identity_id_identity_profiles")),
        sa.ForeignKeyConstraint(["professor_id"], ["professors.id"], name=op.f("fk_email_send_attempts_professor_id_professors")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_send_attempts")),
        sa.UniqueConstraint("attempt_key", name="uq_email_send_attempts_attempt_key"),
        sa.UniqueConstraint("email_task_id", "idempotency_key", name="uq_email_send_attempts_task_idempotency_key"),
    )
    op.create_index("ix_email_send_attempts_email_task_id", "email_send_attempts", ["email_task_id"], unique=False)
    op.create_index("ix_email_send_attempts_phase", "email_send_attempts", ["phase"], unique=False)
    op.create_index("ix_email_send_attempts_lease_expires_at", "email_send_attempts", ["lease_expires_at"], unique=False)
    op.create_index("ix_email_send_attempts_rfc_message_id", "email_send_attempts", ["rfc_message_id"], unique=False)

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=255), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("side_effect_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_entity_type", sa.String(length=64), nullable=True),
        sa.Column("result_entity_id", sa.String(length=64), nullable=True),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.JSON(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_records")),
        sa.UniqueConstraint("scope", "key", name="uq_idempotency_records_scope_key"),
    )
    op.create_index("ix_idempotency_records_key", "idempotency_records", ["key"], unique=False)
    op.create_index("ix_idempotency_records_status", "idempotency_records", ["status"], unique=False)
    op.create_index("ix_idempotency_records_lease_expires_at", "idempotency_records", ["lease_expires_at"], unique=False)

    op.add_column("email_logs", sa.Column("send_attempt_id", sa.Integer(), nullable=True))
    op.add_column("email_logs", sa.Column("send_attempt_key", sa.String(length=128), nullable=True))
    op.add_column("email_logs", sa.Column("idempotency_key", sa.String(length=255), nullable=True))
    op.create_foreign_key(
        op.f("fk_email_logs_send_attempt_id_email_send_attempts"),
        "email_logs",
        "email_send_attempts",
        ["send_attempt_id"],
        ["id"],
    )
    op.create_index(op.f("ix_email_logs_send_attempt_id"), "email_logs", ["send_attempt_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_email_logs_send_attempt_id"), table_name="email_logs")
    op.drop_constraint(op.f("fk_email_logs_send_attempt_id_email_send_attempts"), "email_logs", type_="foreignkey")
    op.drop_column("email_logs", "idempotency_key")
    op.drop_column("email_logs", "send_attempt_key")
    op.drop_column("email_logs", "send_attempt_id")

    op.drop_index("ix_idempotency_records_lease_expires_at", table_name="idempotency_records")
    op.drop_index("ix_idempotency_records_status", table_name="idempotency_records")
    op.drop_index("ix_idempotency_records_key", table_name="idempotency_records")
    op.drop_table("idempotency_records")

    op.drop_index("ix_email_send_attempts_rfc_message_id", table_name="email_send_attempts")
    op.drop_index("ix_email_send_attempts_lease_expires_at", table_name="email_send_attempts")
    op.drop_index("ix_email_send_attempts_phase", table_name="email_send_attempts")
    op.drop_index("ix_email_send_attempts_email_task_id", table_name="email_send_attempts")
    op.drop_table("email_send_attempts")
    op.drop_column("email_tasks", "send_generation")
```

- [ ] **步骤 5：更新 schema 测试**

在 `backend/test/test_database_schema.py::DatabaseSchemaTests.test_runtime_tables_and_columns_are_created` 中扩展断言：

```python
self.assertTrue(
    {
        "email_send_attempts",
        "idempotency_records",
    }.issubset(table_names),
)
self.assertIn("send_generation", task_columns)
self.assertIn("send_attempt_id", log_columns)
self.assertIn("send_attempt_key", log_columns)
self.assertIn("idempotency_key", log_columns)

send_attempt_columns = self._get_columns("email_send_attempts")
self.assertTrue(
    {
        "id",
        "email_task_id",
        "attempt_key",
        "idempotency_key",
        "rfc_message_id",
        "phase",
        "lease_owner",
        "lease_expires_at",
        "smtp_started_at",
        "smtp_accepted_at",
        "verification_confidence",
        "verification_evidence",
        "provider_payload",
        "error_summary",
    }.issubset(send_attempt_columns),
)

idempotency_columns = self._get_columns("idempotency_records")
self.assertTrue(
    {
        "id",
        "scope",
        "key",
        "request_fingerprint",
        "status",
        "lease_owner",
        "lease_expires_at",
        "side_effect_started_at",
        "result_entity_type",
        "result_entity_id",
        "response_status_code",
        "response_body",
        "error_summary",
    }.issubset(idempotency_columns),
)
```

- [ ] **步骤 6：运行测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_email_send_idempotency_models backend.test.test_database_schema -v
```

预期：
新增模型测试通过，schema 测试通过。

- [ ] **步骤 7：提交**

```bash
git add backend/app/models/email_send_attempt.py backend/app/models/idempotency_record.py backend/app/models/email_task.py backend/app/models/email_log.py backend/app/models/__init__.py backend/alembic/versions/20260707_email_send_idempotency.py backend/test/test_email_send_idempotency_models.py backend/test/test_database_schema.py
git commit -m "feat(email): add send attempt and idempotency models"
```

### 任务 2：请求幂等服务

**文件：**
- 创建：`backend/app/services/idempotency.py`
- 创建：`backend/test/test_idempotency_service.py`

- [ ] **步骤 1：编写失败测试**

创建 `backend/test/test_idempotency_service.py`：

```python
from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, IdempotencyRecordStatus
from app.services.idempotency import (
    IdempotencyDecision,
    begin_idempotent_operation,
    complete_idempotency_record,
    fingerprint_idempotent_request,
    mark_idempotency_side_effect_started,
)


class IdempotencyServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self._run_async(self._create_schema())

    def tearDown(self) -> None:
        self._run_async(self.engine.dispose())

    def _run_async(self, awaitable):
        return asyncio.run(awaitable)

    async def _create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    def test_same_key_and_body_returns_completed_response(self) -> None:
        now = datetime(2026, 7, 7, 10, 0, tzinfo=UTC)
        fingerprint = fingerprint_idempotent_request(
            method="POST",
            path="/api/email-tasks/1/approve-and-send",
            body={"subject": "Hello", "body_text": "Body"},
        )

        async def scenario() -> tuple[str, dict[str, object] | None]:
            async with self.session_factory() as session:
                first = await begin_idempotent_operation(
                    session,
                    scope="POST /api/email-tasks/1/approve-and-send",
                    key="key-1",
                    request_fingerprint=fingerprint,
                    lease_owner="worker-1",
                    now=now,
                )
                await complete_idempotency_record(
                    session,
                    first.record,
                    response_status_code=200,
                    response_body={"ok": True},
                    result_entity_type="email_task",
                    result_entity_id="1",
                    now=now,
                )
                await session.commit()

            async with self.session_factory() as session:
                second = await begin_idempotent_operation(
                    session,
                    scope="POST /api/email-tasks/1/approve-and-send",
                    key="key-1",
                    request_fingerprint=fingerprint,
                    lease_owner="worker-2",
                    now=now + timedelta(seconds=1),
                )
                return second.decision.value, second.record.response_body

        self.assertEqual(self._run_async(scenario()), (IdempotencyDecision.RETURN_COMPLETED.value, {"ok": True}))

    def test_same_key_with_different_fingerprint_conflicts(self) -> None:
        now = datetime(2026, 7, 7, 10, 0, tzinfo=UTC)

        async def scenario() -> str:
            async with self.session_factory() as session:
                first = await begin_idempotent_operation(
                    session,
                    scope="POST /api/batch-tasks",
                    key="key-1",
                    request_fingerprint="sha256:first",
                    lease_owner="worker-1",
                    now=now,
                )
                self.assertEqual(first.decision, IdempotencyDecision.EXECUTE)
                await session.commit()

            async with self.session_factory() as session:
                second = await begin_idempotent_operation(
                    session,
                    scope="POST /api/batch-tasks",
                    key="key-1",
                    request_fingerprint="sha256:second",
                    lease_owner="worker-2",
                    now=now + timedelta(seconds=1),
                )
                return second.decision.value

        self.assertEqual(self._run_async(scenario()), IdempotencyDecision.CONFLICT.value)

    def test_expired_before_side_effect_can_be_taken_over(self) -> None:
        now = datetime(2026, 7, 7, 10, 0, tzinfo=UTC)

        async def scenario() -> tuple[str, str | None]:
            async with self.session_factory() as session:
                first = await begin_idempotent_operation(
                    session,
                    scope="POST /api/batch-tasks",
                    key="key-2",
                    request_fingerprint="sha256:same",
                    lease_owner="worker-1",
                    now=now,
                    lease_seconds=30,
                )
                await session.commit()
                self.assertEqual(first.decision, IdempotencyDecision.EXECUTE)

            async with self.session_factory() as session:
                second = await begin_idempotent_operation(
                    session,
                    scope="POST /api/batch-tasks",
                    key="key-2",
                    request_fingerprint="sha256:same",
                    lease_owner="worker-2",
                    now=now + timedelta(seconds=31),
                    lease_seconds=30,
                )
                await session.commit()
                return second.decision.value, second.record.lease_owner

        self.assertEqual(self._run_async(scenario()), (IdempotencyDecision.EXECUTE.value, "worker-2"))

    def test_expired_after_side_effect_started_returns_recovering(self) -> None:
        now = datetime(2026, 7, 7, 10, 0, tzinfo=UTC)

        async def scenario() -> tuple[str, str]:
            async with self.session_factory() as session:
                first = await begin_idempotent_operation(
                    session,
                    scope="POST /api/email-tasks/1/approve-and-send",
                    key="key-3",
                    request_fingerprint="sha256:same",
                    lease_owner="worker-1",
                    now=now,
                    lease_seconds=30,
                )
                await mark_idempotency_side_effect_started(session, first.record, now=now)
                await session.commit()

            async with self.session_factory() as session:
                second = await begin_idempotent_operation(
                    session,
                    scope="POST /api/email-tasks/1/approve-and-send",
                    key="key-3",
                    request_fingerprint="sha256:same",
                    lease_owner="worker-2",
                    now=now + timedelta(seconds=31),
                    lease_seconds=30,
                )
                return second.decision.value, second.record.status

        self.assertEqual(
            self._run_async(scenario()),
            (IdempotencyDecision.RETURN_RECOVERING.value, IdempotencyRecordStatus.RECOVERING.value),
        )
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_idempotency_service -v
```

预期：
`ImportError`，因为 `app.services.idempotency` 尚不存在。

- [ ] **步骤 3：实现 `idempotency.py`**

创建 `backend/app/services/idempotency.py`：

```python
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import as_utc_aware
from app.models import IdempotencyRecord, IdempotencyRecordStatus

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
DEFAULT_IDEMPOTENCY_LEASE_SECONDS = 120


class IdempotencyDecision(StrEnum):
    EXECUTE = "execute"
    RETURN_IN_PROGRESS = "return_in_progress"
    RETURN_COMPLETED = "return_completed"
    RETURN_RECOVERING = "return_recovering"
    RETURN_UNKNOWN = "return_unknown"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class IdempotencyBeginResult:
    decision: IdempotencyDecision
    record: IdempotencyRecord


def fingerprint_idempotent_request(*, method: str, path: str, body: Any) -> str:
    payload = {
        "method": method.upper(),
        "path": path,
        "body": body,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()
    return f"sha256:{digest}"


def normalize_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    key = value.strip()
    if not key:
        return None
    if len(key) > 255:
        raise ValueError("Idempotency-Key 长度不能超过 255 个字符")
    return key


def new_worker_owner(prefix: str) -> str:
    return f"{prefix}:{uuid.uuid4()}"


async def begin_idempotent_operation(
    session: AsyncSession,
    *,
    scope: str,
    key: str,
    request_fingerprint: str,
    lease_owner: str,
    now: datetime,
    lease_seconds: int = DEFAULT_IDEMPOTENCY_LEASE_SECONDS,
) -> IdempotencyBeginResult:
    resolved_now = as_utc_aware(now)
    lease_expires_at = resolved_now + timedelta(seconds=lease_seconds)
    existing = await session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.key == key,
        ),
    )
    if existing is None:
        record = IdempotencyRecord(
            scope=scope,
            key=key,
            request_fingerprint=request_fingerprint,
            status=IdempotencyRecordStatus.IN_PROGRESS.value,
            lease_owner=lease_owner,
            lease_expires_at=lease_expires_at,
            created_at=resolved_now,
            updated_at=resolved_now,
        )
        session.add(record)
        await session.flush()
        return IdempotencyBeginResult(IdempotencyDecision.EXECUTE, record)

    if existing.request_fingerprint != request_fingerprint:
        return IdempotencyBeginResult(IdempotencyDecision.CONFLICT, existing)

    if existing.status == IdempotencyRecordStatus.COMPLETED.value:
        return IdempotencyBeginResult(IdempotencyDecision.RETURN_COMPLETED, existing)
    if existing.status == IdempotencyRecordStatus.COMPLETED_UNKNOWN.value:
        return IdempotencyBeginResult(IdempotencyDecision.RETURN_UNKNOWN, existing)
    if existing.status in {
        IdempotencyRecordStatus.SIDE_EFFECT_STARTED.value,
        IdempotencyRecordStatus.RECOVERING.value,
    }:
        existing.status = IdempotencyRecordStatus.RECOVERING.value
        existing.updated_at = resolved_now
        await session.flush()
        return IdempotencyBeginResult(IdempotencyDecision.RETURN_RECOVERING, existing)

    lease_active = existing.lease_expires_at is not None and as_utc_aware(existing.lease_expires_at) > resolved_now
    if lease_active:
        return IdempotencyBeginResult(IdempotencyDecision.RETURN_IN_PROGRESS, existing)

    if existing.side_effect_started_at is not None:
        existing.status = IdempotencyRecordStatus.RECOVERING.value
        existing.updated_at = resolved_now
        await session.flush()
        return IdempotencyBeginResult(IdempotencyDecision.RETURN_RECOVERING, existing)

    existing.status = IdempotencyRecordStatus.IN_PROGRESS.value
    existing.lease_owner = lease_owner
    existing.lease_expires_at = lease_expires_at
    existing.updated_at = resolved_now
    await session.flush()
    return IdempotencyBeginResult(IdempotencyDecision.EXECUTE, existing)


async def mark_idempotency_side_effect_started(
    session: AsyncSession,
    record: IdempotencyRecord,
    *,
    now: datetime,
) -> None:
    resolved_now = as_utc_aware(now)
    record.status = IdempotencyRecordStatus.SIDE_EFFECT_STARTED.value
    record.side_effect_started_at = resolved_now
    record.updated_at = resolved_now
    await session.flush()


async def complete_idempotency_record(
    session: AsyncSession,
    record: IdempotencyRecord,
    *,
    response_status_code: int,
    response_body: dict[str, object],
    result_entity_type: str,
    result_entity_id: str,
    now: datetime,
) -> None:
    resolved_now = as_utc_aware(now)
    record.status = IdempotencyRecordStatus.COMPLETED.value
    record.response_status_code = response_status_code
    record.response_body = response_body
    record.result_entity_type = result_entity_type
    record.result_entity_id = result_entity_id
    record.lease_expires_at = None
    record.updated_at = resolved_now
    await session.flush()


async def fail_idempotency_before_side_effect(
    session: AsyncSession,
    record: IdempotencyRecord,
    *,
    error_summary: str,
    now: datetime,
) -> None:
    resolved_now = as_utc_aware(now)
    record.status = IdempotencyRecordStatus.FAILED_BEFORE_SIDE_EFFECT.value
    record.error_summary = error_summary
    record.lease_expires_at = None
    record.updated_at = resolved_now
    await session.flush()
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_idempotency_service -v
```

预期：
全部通过。

- [ ] **步骤 5：提交**

```bash
git add backend/app/services/idempotency.py backend/test/test_idempotency_service.py
git commit -m "feat(email): add idempotency service"
```

### 任务 3：发送 attempt 服务

**文件：**
- 创建：`backend/app/services/email_send_attempts.py`
- 创建：`backend/test/test_email_send_attempts.py`

- [ ] **步骤 1：编写失败测试**

创建 `backend/test/test_email_send_attempts.py`：

```python
from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, EmailSendAttemptPhase, EmailTask
from app.services.email_send_attempts import (
    EmailSendSnapshot,
    attachment_fingerprint,
    build_auto_send_idempotency_key,
    build_send_attempt_key,
    prepare_email_send_attempt,
)


class EmailSendAttemptServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self._run_async(self._create_schema())

    def tearDown(self) -> None:
        self._run_async(self.engine.dispose())

    def _run_async(self, awaitable):
        return asyncio.run(awaitable)

    async def _create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    def test_attempt_key_uses_task_generation(self) -> None:
        task = EmailTask(id=42, identity_id=1, llm_profile_id=1, professor_id=2, send_generation=3)
        self.assertEqual(build_send_attempt_key(task), "email-task:42:generation:3")
        self.assertEqual(build_auto_send_idempotency_key(task), "auto-send:42:3")

    def test_prepare_attempt_generates_stable_message_id_before_smtp(self) -> None:
        now = datetime(2026, 7, 7, 10, 0, tzinfo=UTC)

        async def scenario() -> tuple[str, str, str]:
            async with self.session_factory() as session:
                task = EmailTask(id=42, identity_id=1, llm_profile_id=1, professor_id=2, send_generation=1)
                session.add(task)
                await session.flush()
                attempt = await prepare_email_send_attempt(
                    session,
                    task=task,
                    snapshot=EmailSendSnapshot(
                        recipient_email="teacher@example.edu",
                        subject="Hello",
                        body_text="Body",
                        body_html="<p>Body</p>",
                        attachment_paths=(),
                    ),
                    idempotency_key="auto-send:42:1",
                    lease_owner="worker-1",
                    now=now,
                )
                await session.commit()
                return attempt.attempt_key, attempt.phase, attempt.rfc_message_id

        attempt_key, phase, message_id = self._run_async(scenario())
        self.assertEqual(attempt_key, "email-task:42:generation:1")
        self.assertEqual(phase, EmailSendAttemptPhase.PREPARED.value)
        self.assertTrue(message_id.startswith("<"))
        self.assertTrue(message_id.endswith(">"))

    def test_attachment_fingerprint_is_stable_for_same_paths(self) -> None:
        self.assertEqual(
            attachment_fingerprint(["/tmp/a.pdf", "/tmp/b.pdf"]),
            attachment_fingerprint(("/tmp/a.pdf", "/tmp/b.pdf")),
        )
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_email_send_attempts -v
```

预期：
`ImportError`，因为 `app.services.email_send_attempts` 尚不存在。

- [ ] **步骤 3：实现 attempt 服务**

创建 `backend/app/services/email_send_attempts.py`：

```python
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import make_msgid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import as_utc_aware
from app.models import EmailSendAttempt, EmailSendAttemptPhase, EmailTask

SEND_ATTEMPT_LEASE_SECONDS = 300


@dataclass(frozen=True, slots=True)
class EmailSendSnapshot:
    recipient_email: str | None
    subject: str
    body_text: str
    body_html: str | None
    attachment_paths: tuple[str, ...]


def build_send_attempt_key(task: EmailTask) -> str:
    return f"email-task:{task.id}:generation:{task.send_generation}"


def build_auto_send_idempotency_key(task: EmailTask) -> str:
    return f"auto-send:{task.id}:{task.send_generation}"


def body_fingerprint(snapshot: EmailSendSnapshot) -> str:
    payload = {
        "subject": snapshot.subject,
        "body_text": snapshot.body_text,
        "body_html": snapshot.body_html,
        "recipient_email": snapshot.recipient_email,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()
    return f"sha256:{digest}"


def attachment_fingerprint(paths: tuple[str, ...] | list[str]) -> str:
    digest = hashlib.sha256(
        json.dumps(list(paths), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()
    return f"sha256:{digest}"


def make_rfc_message_id_for_task(task: EmailTask) -> str:
    domain = "auto-email-sender.local"
    identity_email = getattr(task.identity, "email_address", None)
    if isinstance(identity_email, str) and "@" in identity_email:
        domain = identity_email.rsplit("@", 1)[1]
    return make_msgid(domain=domain)


async def prepare_email_send_attempt(
    session: AsyncSession,
    *,
    task: EmailTask,
    snapshot: EmailSendSnapshot,
    idempotency_key: str,
    lease_owner: str,
    now: datetime,
) -> EmailSendAttempt:
    attempt_key = build_send_attempt_key(task)
    existing = await session.scalar(
        select(EmailSendAttempt).where(EmailSendAttempt.attempt_key == attempt_key),
    )
    if existing is not None:
        return existing

    resolved_now = as_utc_aware(now)
    attempt = EmailSendAttempt(
        email_task_id=task.id,
        attempt_key=attempt_key,
        idempotency_key=idempotency_key,
        rfc_message_id=make_rfc_message_id_for_task(task),
        identity_id=task.identity_id,
        professor_id=task.professor_id,
        recipient_email=snapshot.recipient_email,
        subject=snapshot.subject,
        body_fingerprint=body_fingerprint(snapshot),
        attachment_fingerprint=attachment_fingerprint(snapshot.attachment_paths),
        phase=EmailSendAttemptPhase.PREPARED.value,
        lease_owner=lease_owner,
        lease_expires_at=resolved_now + timedelta(seconds=SEND_ATTEMPT_LEASE_SECONDS),
        heartbeat_at=resolved_now,
        created_at=resolved_now,
        updated_at=resolved_now,
    )
    session.add(attempt)
    await session.flush()
    return attempt


async def mark_attempt_smtp_inflight(
    session: AsyncSession,
    attempt: EmailSendAttempt,
    *,
    lease_owner: str,
    now: datetime,
) -> None:
    resolved_now = as_utc_aware(now)
    attempt.phase = EmailSendAttemptPhase.SMTP_INFLIGHT.value
    attempt.smtp_started_at = resolved_now
    attempt.lease_owner = lease_owner
    attempt.lease_expires_at = resolved_now + timedelta(seconds=SEND_ATTEMPT_LEASE_SECONDS)
    attempt.heartbeat_at = resolved_now
    attempt.updated_at = resolved_now
    await session.flush()


async def mark_attempt_smtp_accepted(
    session: AsyncSession,
    attempt: EmailSendAttempt,
    *,
    provider_payload: dict[str, object],
    now: datetime,
) -> None:
    resolved_now = as_utc_aware(now)
    attempt.phase = EmailSendAttemptPhase.SMTP_ACCEPTED.value
    attempt.smtp_accepted_at = resolved_now
    attempt.provider_payload = provider_payload
    attempt.updated_at = resolved_now
    await session.flush()


async def mark_attempt_completed(
    session: AsyncSession,
    attempt: EmailSendAttempt,
    *,
    provider_payload: dict[str, object] | None,
    error_summary: str | None,
    now: datetime,
) -> None:
    resolved_now = as_utc_aware(now)
    attempt.phase = EmailSendAttemptPhase.COMPLETED.value
    if provider_payload is not None:
        attempt.provider_payload = provider_payload
    attempt.error_summary = error_summary
    attempt.lease_expires_at = None
    attempt.updated_at = resolved_now
    await session.flush()
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_email_send_attempts -v
```

预期：
全部通过。

- [ ] **步骤 5：提交**

```bash
git add backend/app/services/email_send_attempts.py backend/test/test_email_send_attempts.py
git commit -m "feat(email): add send attempt service"
```

### 任务 4：拆分 SMTP 投递和已发送箱同步

**文件：**
- 修改：`backend/app/services/mail_runtime.py`
- 修改：`backend/test/test_mail_runtime.py`

- [ ] **步骤 1：编写失败测试**

在 `backend/test/test_mail_runtime.py` 中增加测试：

```python
def test_send_email_uses_supplied_message_id(self) -> None:
    smtp_client = _FakeSmtpClient()
    identity = self._build_identity(imap_host=None, imap_port=None, imap_username=None, imap_password=None)
    professor = self._build_professor()

    with patch("app.services.mail_runtime._open_smtp_client", return_value=smtp_client):
        result = self._run_async(
            mail_runtime.send_email(
                identity=identity,
                professor=professor,
                subject="Hello",
                body_text="Body",
                body_html=None,
                attachments=[],
                rfc_message_id="<stable@example.com>",
                sync_sent_folder=False,
            ),
        )

    self.assertEqual(result.message_id, "<stable@example.com>")
    self.assertEqual(smtp_client.messages[0]["Message-ID"], "<stable@example.com>")
    self.assertEqual(result.provider_payload["sent_folder_sync"]["status"], "deferred")


def test_sync_sent_folder_for_message_does_not_call_smtp(self) -> None:
    identity = self._build_identity()
    professor = self._build_professor()
    imap_client = _FakeImapClient(
        list_payload=[b'(\\Sent) "/" "Sent"'],
        search_data=b"",
    )

    with (
        patch("app.services.mail_runtime._open_imap_client", return_value=imap_client),
        patch("app.services.mail_runtime._open_smtp_client") as smtp_factory,
    ):
        result = self._run_async(
            mail_runtime.sync_sent_folder_for_message(
                identity=identity,
                professor=professor,
                subject="Hello",
                body_text="Body",
                body_html=None,
                attachments=[],
                rfc_message_id="<stable@example.com>",
            ),
        )

    smtp_factory.assert_not_called()
    self.assertEqual(result.status, "appended")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_mail_runtime -v
```

预期：
新增测试失败，提示 `send_email` 不接受 `rfc_message_id`、`sync_sent_folder`，且 `sync_sent_folder_for_message` 不存在。

- [ ] **步骤 3：修改 `mail_runtime.py`**

修改 `build_email_message`：

```python
def build_email_message(
    *,
    identity: IdentityProfile,
    professor: Professor,
    subject: str,
    body_text: str,
    body_html: str | None,
    attachments: list[MailAttachment],
    rfc_message_id: str | None = None,
) -> EmailMessage:
    from app.services.outreach_templates import get_identity_sender_name

    message = EmailMessage()
    message["From"] = formataddr((get_identity_sender_name(identity), identity.email_address))
    message["To"] = professor.email or ""
    message["Subject"] = subject
    message["Message-ID"] = rfc_message_id or make_msgid(domain=identity.email_address.split("@")[-1])
    message["Date"] = email_datetime_now()
    message.set_content(body_text)
    message.add_alternative(body_html or text_to_html(body_text), subtype="html")
```

修改 `_send_email_sync`：

```python
def _send_email_sync(
    identity: IdentityProfile,
    message: EmailMessage,
    *,
    sync_sent_folder: bool = True,
) -> SentFolderSyncResult:
    server = None
    try:
        server = _open_smtp_client(identity)
        server.login(identity.smtp_username, identity.smtp_password)
        server.send_message(message)
    except (OSError, smtplib.SMTPException, SocketTimeout) as exc:
        raise MailRuntimeError(f"SMTP 发信失败: {exc}") from exc
    finally:
        if server is not None:
            try:
                server.quit()
            except OSError:
                pass
    if not sync_sent_folder:
        return SentFolderSyncResult(status="deferred")
    return _inspect_sent_folder_after_smtp_send_sync(identity, message)
```

修改 `send_email` 签名和调用：

```python
async def send_email(
    *,
    identity: IdentityProfile,
    professor: Professor,
    subject: str,
    body_text: str,
    body_html: str | None,
    attachments: list[MailAttachment],
    rfc_message_id: str | None = None,
    sync_sent_folder: bool = True,
) -> SendMailResult:
    if not professor.email:
        raise MailRuntimeError("导师没有可用邮箱，无法发送")

    message = build_email_message(
        identity=identity,
        professor=professor,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        attachments=attachments,
        rfc_message_id=rfc_message_id,
    )
    sent_folder_sync = await asyncio.to_thread(
        _send_email_sync,
        identity,
        message,
        sync_sent_folder=sync_sent_folder,
    )
    return SendMailResult(
        message_id=str(message["Message-ID"]),
        provider_payload={
            "smtp_host": identity.smtp_host,
            "smtp_port": identity.smtp_port,
            "to": professor.email,
            "sent_folder_sync": sent_folder_sync.to_payload(),
        },
    )
```

新增后置同步函数：

```python
async def sync_sent_folder_for_message(
    *,
    identity: IdentityProfile,
    professor: Professor,
    subject: str,
    body_text: str,
    body_html: str | None,
    attachments: list[MailAttachment],
    rfc_message_id: str,
) -> SentFolderSyncResult:
    message = build_email_message(
        identity=identity,
        professor=professor,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        attachments=attachments,
        rfc_message_id=rfc_message_id,
    )
    return await asyncio.to_thread(_inspect_sent_folder_after_smtp_send_sync, identity, message)
```

新增 Message-ID 查询函数：

```python
async def sent_folder_contains_message_id(
    identity: IdentityProfile,
    message_id: str,
) -> SentFolderSyncResult:
    if not _identity_has_imap_config(identity):
        return SentFolderSyncResult(status="imap_not_configured")

    def check() -> SentFolderSyncResult:
        client: IMAP4 | IMAP4_SSL | None = None
        try:
            client = _open_imap_client(identity)
            client.login(identity.imap_username or "", identity.imap_password or "")
            _send_imap_client_id(client, identity)
            folder = _select_sent_folder_for_inspection(client)
            if folder is None:
                return SentFolderSyncResult(status="sent_folder_not_found")
            result = _search_selected_mailbox_by_message_id_with_retries(client, message_id)
            if result.uids:
                return SentFolderSyncResult(
                    status="existing_copy_found",
                    folder=folder,
                    matched_uids=result.uids,
                    search_attempts=result.attempts,
                )
            if result.ok:
                return SentFolderSyncResult(status="not_found", folder=folder, search_attempts=result.attempts)
            return SentFolderSyncResult(status="search_failed_skipped", folder=folder, search_attempts=result.attempts)
        except Exception as exc:
            return SentFolderSyncResult(status="error_skipped", detail=f"{type(exc).__name__}: {exc}")
        finally:
            _logout_imap_client(client)

    return await asyncio.to_thread(check)
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_mail_runtime -v
```

预期：
全部通过；既有已发送箱同步测试仍通过，因为默认 `sync_sent_folder=True`。

- [ ] **步骤 5：提交**

```bash
git add backend/app/services/mail_runtime.py backend/test/test_mail_runtime.py
git commit -m "feat(email): defer sent folder sync after smtp"
```

### 任务 5：dispatch 使用 attempt 并先落库 sent

**文件：**
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/test/test_batch_task_dispatch_schedule.py`

- [ ] **步骤 1：编写失败测试**

在 `backend/test/test_batch_task_dispatch_schedule.py` 增加测试：

```python
def test_dispatch_email_task_uses_attempt_message_id(self) -> None:
    task_id = self._run_async(self._create_manual_approved_task())

    async def fake_send_email(**kwargs):
        return SendMailResult(
            message_id=kwargs["rfc_message_id"],
            provider_payload={
                "smtp_host": "smtp.example.com",
                "smtp_port": 465,
                "to": "teacher@example.edu",
                "sent_folder_sync": {"status": "deferred"},
            },
        )

    with patch("app.services.task_runtime.mail_runtime.send_email", new=AsyncMock(side_effect=fake_send_email)) as mocked_send:
        dispatched = self._run_async(dispatch_email_task(self.session_factory, task_id))

    self.assertTrue(dispatched)
    self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.SENT.value)
    sent_kwargs = mocked_send.await_args.kwargs
    self.assertEqual(sent_kwargs["sync_sent_folder"], False)
    self.assertTrue(sent_kwargs["rfc_message_id"].startswith("<"))
    self.assertEqual(
        self._run_async(self._get_task_last_message_id(task_id)),
        sent_kwargs["rfc_message_id"],
    )


def test_sent_folder_sync_failure_does_not_revert_sent_task(self) -> None:
    task_id = self._run_async(self._create_manual_approved_task())

    with (
        patch("app.services.task_runtime.mail_runtime.send_email", new=AsyncMock(return_value=self._build_send_result())),
        patch(
            "app.services.task_runtime.mail_runtime.sync_sent_folder_for_message",
            new=AsyncMock(side_effect=RuntimeError("imap unavailable")),
        ),
    ):
        dispatched = self._run_async(dispatch_email_task(self.session_factory, task_id))

    self.assertTrue(dispatched)
    self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.SENT.value)
    self.assertEqual(self._run_async(self._count_email_logs(task_id, EmailDirection.SENT.value)), 1)
```

在同一测试类增加 helper：

```python
async def _get_task_last_message_id(self, task_id: int) -> str | None:
    async with self.session_factory() as session:
        task = await session.get(EmailTask, task_id)
        assert task is not None
        return task.last_rfc_message_id
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_batch_task_dispatch_schedule.BatchTaskDispatchScheduleTests.test_dispatch_email_task_uses_attempt_message_id backend.test.test_batch_task_dispatch_schedule.BatchTaskDispatchScheduleTests.test_sent_folder_sync_failure_does_not_revert_sent_task -v
```

预期：
失败，旧链路不传 `rfc_message_id`、不传 `sync_sent_folder=False`，且已发送箱同步仍在 SMTP 成功返回之前执行。

- [ ] **步骤 3：改造 `dispatch_email_task`**

在 `backend/app/services/task_runtime.py` import 新服务：

```python
from app.services.email_send_attempts import (
    EmailSendSnapshot,
    build_auto_send_idempotency_key,
    mark_attempt_completed,
    mark_attempt_smtp_accepted,
    mark_attempt_smtp_inflight,
    prepare_email_send_attempt,
)
from app.services.idempotency import new_worker_owner
```

修改 `dispatch_email_task` 签名：

```python
async def dispatch_email_task(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    now: datetime | None = None,
    respect_identity_send_window: bool = True,
    idempotency_key: str | None = None,
) -> bool:
```

在渲染主题、正文、附件后，SMTP 前创建 attempt：

```python
        worker_owner = new_worker_owner("email-dispatch")
        resolved_idempotency_key = idempotency_key or build_auto_send_idempotency_key(task)
        attempt = await prepare_email_send_attempt(
            session,
            task=task,
            snapshot=EmailSendSnapshot(
                recipient_email=task.professor.email,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                attachment_paths=tuple(attachment.file_path for attachment in attachments),
            ),
            idempotency_key=resolved_idempotency_key,
            lease_owner=worker_owner,
            now=utc_now(),
        )
        await session.commit()

        await mark_attempt_smtp_inflight(
            session,
            attempt,
            lease_owner=worker_owner,
            now=utc_now(),
        )
        await session.commit()
```

修改 SMTP 调用：

```python
            result = await mail_runtime.send_email(
                identity=task.identity,
                professor=task.professor,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                attachments=attachments,
                rfc_message_id=attempt.rfc_message_id,
                sync_sent_folder=False,
            )
```

SMTP 成功后先更新 attempt、task 和 `EmailLog`，再 commit：

```python
            await mark_attempt_smtp_accepted(
                session,
                attempt,
                provider_payload=provider_payload,
                now=utc_now(),
            )
            task.status = EmailTaskStatus.SENT.value
            task.sent_at = utc_now()
            task.last_rfc_message_id = rfc_message_id
            task.last_error = None
            task.updated_at = utc_now()
            session.add(
                EmailLog(
                    email_task_id=task.id,
                    send_attempt_id=attempt.id,
                    send_attempt_key=attempt.attempt_key,
                    idempotency_key=attempt.idempotency_key,
                    identity_id=task.identity_id,
                    llm_profile_id=task.llm_profile_id,
                    professor_id=task.professor_id,
                    direction=EmailDirection.SENT.value,
                    subject=subject,
                    content=body_text,
                    content_html=body_html,
                    rfc_message_id=rfc_message_id,
                    provider_payload=provider_payload,
                ),
            )
```

在 `await session.commit()` 后执行后置同步：

```python
        if task.status == EmailTaskStatus.SENT.value and rfc_message_id:
            sync_error = None
            sync_payload = None
            try:
                sync_result = await mail_runtime.sync_sent_folder_for_message(
                    identity=task.identity,
                    professor=task.professor,
                    subject=subject,
                    body_text=body_text,
                    body_html=body_html,
                    attachments=attachments,
                    rfc_message_id=rfc_message_id,
                )
                sync_payload = {"sent_folder_sync": sync_result.to_payload()}
            except Exception as exc:
                sync_error = f"{type(exc).__name__}: {exc}"
                sync_payload = {"sent_folder_sync": {"status": "error_skipped", "detail": sync_error}}

            await mark_attempt_completed(
                session,
                attempt,
                provider_payload={**(attempt.provider_payload or {}), **sync_payload},
                error_summary=sync_error,
                now=utc_now(),
            )
            await session.commit()
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_batch_task_dispatch_schedule -v
```

预期：
全部通过。

- [ ] **步骤 5：提交**

```bash
git add backend/app/services/task_runtime.py backend/test/test_batch_task_dispatch_schedule.py
git commit -m "feat(email): send tasks through persisted attempts"
```

### 任务 6：中断恢复和系统核验

**文件：**
- 创建：`backend/app/services/email_send_verification.py`
- 修改：`backend/app/services/task_runtime.py`
- 创建：`backend/test/test_email_send_recovery.py`

- [ ] **步骤 1：编写失败测试**

创建 `backend/test/test_email_send_recovery.py`，复用 `test.schema_database.create_schema_sqlite_database` 建真实 SQLite schema：

```python
from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    EmailSendAttempt,
    EmailSendAttemptPhase,
    EmailTask,
    EmailTaskSource,
    EmailTaskStatus,
    IdentityProfile,
    LLMProfile,
    Professor,
)
from app.services import mail_runtime
from app.services.task_runtime import recover_stale_sending_tasks
from test.schema_database import create_schema_sqlite_database


class EmailSendRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "send_recovery.db"
        create_schema_sqlite_database(self.db_path)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.db_path.as_posix()}", future=True)
        self.session_factory = async_sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)

    def tearDown(self) -> None:
        self._run_async(self.engine.dispose())
        self.temp_dir.cleanup()

    def _run_async(self, awaitable):
        return asyncio.run(awaitable)

    def test_prepared_attempt_can_be_restored_to_dispatchable(self) -> None:
        now = datetime(2026, 7, 7, 10, 0, tzinfo=UTC)
        task_id = self._run_async(
            self._create_sending_task_with_attempt(
                phase=EmailSendAttemptPhase.PREPARED.value,
                lease_expires_at=now - timedelta(minutes=1),
            ),
        )

        recovered = self._run_async(recover_stale_sending_tasks(self.session_factory, now=now))

        self.assertEqual(recovered, 1)
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.APPROVED.value)

    def test_smtp_inflight_attempt_moves_to_confirming_without_resend(self) -> None:
        now = datetime(2026, 7, 7, 10, 0, tzinfo=UTC)
        task_id = self._run_async(
            self._create_sending_task_with_attempt(
                phase=EmailSendAttemptPhase.SMTP_INFLIGHT.value,
                lease_expires_at=now - timedelta(minutes=1),
            ),
        )

        with patch("app.services.task_runtime.mail_runtime.send_email", new=AsyncMock()) as mocked_send:
            recovered = self._run_async(recover_stale_sending_tasks(self.session_factory, now=now))

        self.assertEqual(recovered, 1)
        mocked_send.assert_not_awaited()
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.SEND_CONFIRMING.value)

    def test_smtp_accepted_attempt_recovers_to_sent(self) -> None:
        now = datetime(2026, 7, 7, 10, 0, tzinfo=UTC)
        task_id = self._run_async(
            self._create_sending_task_with_attempt(
                phase=EmailSendAttemptPhase.SMTP_ACCEPTED.value,
                lease_expires_at=now - timedelta(minutes=1),
                provider_payload={"smtp_host": "smtp.example.com"},
            ),
        )

        recovered = self._run_async(recover_stale_sending_tasks(self.session_factory, now=now))

        self.assertEqual(recovered, 1)
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.SENT.value)

    def test_unknown_inflight_attempt_becomes_unconfirmed_after_verification_limit(self) -> None:
        now = datetime(2026, 7, 7, 10, 0, tzinfo=UTC)
        task_id = self._run_async(
            self._create_sending_task_with_attempt(
                phase=EmailSendAttemptPhase.SMTP_INFLIGHT.value,
                lease_expires_at=now - timedelta(minutes=1),
                verification_attempt_count=3,
            ),
        )

        with patch(
            "app.services.email_send_verification.mail_runtime.sent_folder_contains_message_id",
            new=AsyncMock(return_value=mail_runtime.SentFolderSyncResult(status="not_found")),
        ):
            recovered = self._run_async(recover_stale_sending_tasks(self.session_factory, now=now))

        self.assertEqual(recovered, 1)
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.SEND_UNCONFIRMED.value)
```

在同一文件实现这些 helper，字段值按 `backend/test/test_batch_task_dispatch_schedule.py` 的 `_create_manual_approved_task` 使用的最小合法身份、LLM、老师字段填写，确保真实外键存在：

```python
async def _create_sending_task_with_attempt(
    self,
    *,
    phase: str,
    lease_expires_at: datetime,
    provider_payload: dict[str, object] | None = None,
    verification_attempt_count: int = 0,
) -> int:
    async with self.session_factory() as session:
        identity = IdentityProfile(
            name="默认身份",
            profile_name="默认身份",
            sender_name="学生",
            email_address="student@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="student@example.com",
            smtp_password="secret",
        )
        llm_profile = LLMProfile(name="测试模型", provider="openai", model_name="gpt-test")
        professor = Professor(name="老师", email="teacher@example.edu")
        session.add_all([identity, llm_profile, professor])
        await session.flush()
        task = EmailTask(
            source=EmailTaskSource.MANUAL.value,
            identity_id=identity.id,
            llm_profile_id=llm_profile.id,
            professor_id=professor.id,
            status=EmailTaskStatus.SENDING.value,
            approved_subject="Hello",
            approved_body_text="Body",
            last_send_attempt_at=lease_expires_at - timedelta(minutes=30),
        )
        session.add(task)
        await session.flush()
        session.add(
            EmailSendAttempt(
                email_task_id=task.id,
                attempt_key=f"email-task:{task.id}:generation:1",
                idempotency_key=f"auto-send:{task.id}:1",
                rfc_message_id="<recovery@example.com>",
                identity_id=identity.id,
                professor_id=professor.id,
                recipient_email=professor.email,
                subject="Hello",
                body_fingerprint="sha256:body",
                attachment_fingerprint="sha256:attachments",
                phase=phase,
                lease_owner="worker-1",
                lease_expires_at=lease_expires_at,
                verification_attempt_count=verification_attempt_count,
                provider_payload=provider_payload,
                created_at=lease_expires_at - timedelta(minutes=30),
                updated_at=lease_expires_at - timedelta(minutes=30),
            ),
        )
        await session.commit()
        return task.id

async def _get_task_status(self, task_id: int) -> str:
    async with self.session_factory() as session:
        task = await session.get(EmailTask, task_id)
        assert task is not None
        return task.status
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_email_send_recovery -v
```

预期：
失败，旧 `recover_stale_sending_tasks` 会把所有 stale `sending` 恢复为 `approved/scheduled`。

- [ ] **步骤 3：实现核验服务**

创建 `backend/app/services/email_send_verification.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import as_utc_aware
from app.models import EmailDirection, EmailLog, EmailSendAttempt, EmailSendAttemptPhase, EmailTask
from app.services import mail_runtime
from app.services.email_log_ingestion import normalize_message_id

MAX_SEND_VERIFICATION_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class SendVerificationResult:
    succeeded: bool
    confidence: float
    evidence: dict[str, object]
    exhausted: bool = False


async def verify_send_attempt(
    session: AsyncSession,
    *,
    task: EmailTask,
    attempt: EmailSendAttempt,
    now: datetime,
) -> SendVerificationResult:
    if attempt.phase == EmailSendAttemptPhase.SMTP_ACCEPTED.value:
        return SendVerificationResult(
            succeeded=True,
            confidence=1.0,
            evidence={"source": "attempt_phase", "phase": attempt.phase},
        )

    normalized_message_id = normalize_message_id(attempt.rfc_message_id)
    existing_log = await session.scalar(
        select(EmailLog).where(
            EmailLog.identity_id == attempt.identity_id,
            EmailLog.professor_id == attempt.professor_id,
            EmailLog.direction == EmailDirection.SENT.value,
            EmailLog.rfc_message_id == attempt.rfc_message_id,
        ),
    )
    if existing_log is not None:
        return SendVerificationResult(
            succeeded=True,
            confidence=1.0,
            evidence={"source": "local_email_log", "email_log_id": existing_log.id},
        )

    if normalized_message_id and task.identity is not None:
        sent_folder_result = await mail_runtime.sent_folder_contains_message_id(
            task.identity,
            attempt.rfc_message_id,
        )
        if sent_folder_result.status == "existing_copy_found":
            return SendVerificationResult(
                succeeded=True,
                confidence=1.0,
                evidence={
                    "source": "sent_folder_message_id",
                    "folder": sent_folder_result.folder,
                    "matched_uids": list(sent_folder_result.matched_uids),
                },
            )

        evidence = {
            "source": "sent_folder_message_id",
            "status": sent_folder_result.status,
            "detail": sent_folder_result.detail,
        }
    else:
        evidence = {"source": "local_only", "status": "message_id_missing"}

    exhausted = attempt.verification_attempt_count + 1 >= MAX_SEND_VERIFICATION_ATTEMPTS
    return SendVerificationResult(
        succeeded=False,
        confidence=0.0,
        evidence=evidence,
        exhausted=exhausted,
    )
```

- [ ] **步骤 4：改造 `recover_stale_sending_tasks`**

在 `task_runtime.py` import：

```python
from app.services.email_send_verification import verify_send_attempt
```

将 `recover_stale_sending_tasks` 的处理逻辑改为：

```python
        for task in tasks:
            attempt = await session.scalar(
                select(EmailSendAttempt)
                .where(EmailSendAttempt.email_task_id == task.id)
                .order_by(EmailSendAttempt.created_at.desc(), EmailSendAttempt.id.desc())
            )
            if attempt is None:
                task.status = EmailTaskStatus.SEND_UNCONFIRMED.value
                task.last_error = "发送进程中断，缺少发送尝试记录，系统无法确认是否已投递。"
                task.updated_at = resolved_now
                continue

            if attempt.phase == EmailSendAttemptPhase.PREPARED.value:
                _restore_or_cancel_interrupted_send(task)
                attempt.phase = EmailSendAttemptPhase.FAILED_SAFE.value
                attempt.error_summary = "发送在进入 SMTP 前中断，已安全恢复任务。"
                attempt.updated_at = resolved_now
                task.updated_at = resolved_now
                continue

            task.status = EmailTaskStatus.SEND_CONFIRMING.value
            task.last_error = "发送进程中断，系统正在确认服务商是否已接收邮件。"
            attempt.phase = EmailSendAttemptPhase.CONFIRMING.value
            attempt.verification_started_at = attempt.verification_started_at or resolved_now
            attempt.verification_attempt_count += 1
            verification = await verify_send_attempt(session, task=task, attempt=attempt, now=resolved_now)
            attempt.verification_confidence = verification.confidence
            attempt.verification_evidence = verification.evidence
            attempt.updated_at = resolved_now

            if verification.succeeded:
                task.status = EmailTaskStatus.SENT.value
                task.sent_at = task.sent_at or resolved_now
                task.last_rfc_message_id = attempt.rfc_message_id
                task.last_error = None
                attempt.phase = EmailSendAttemptPhase.COMPLETED.value
                attempt.verification_finished_at = resolved_now
            elif verification.exhausted:
                task.status = EmailTaskStatus.SEND_UNCONFIRMED.value
                task.last_error = "系统多次核验后仍无法确认邮件是否已发送，不会自动重发。"
                attempt.phase = EmailSendAttemptPhase.UNCONFIRMED.value
                attempt.verification_finished_at = resolved_now

            task.updated_at = resolved_now
```

当 verification succeeded 且本地没有 sent `EmailLog` 时，补写一条 `EmailLog`：

```python
                session.add(
                    EmailLog(
                        email_task_id=task.id,
                        send_attempt_id=attempt.id,
                        send_attempt_key=attempt.attempt_key,
                        idempotency_key=attempt.idempotency_key,
                        identity_id=task.identity_id,
                        llm_profile_id=task.llm_profile_id,
                        professor_id=task.professor_id,
                        direction=EmailDirection.SENT.value,
                        subject=attempt.subject,
                        content=task.approved_body_text or task.generated_content_text or "",
                        content_html=task.approved_body_html or task.generated_content_html,
                        rfc_message_id=attempt.rfc_message_id,
                        provider_payload={
                            "recovered": True,
                            "verification_evidence": verification.evidence,
                        },
                    ),
                )
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_email_send_recovery -v
```

预期：
全部通过。

- [ ] **步骤 6：提交**

```bash
git add backend/app/services/email_send_verification.py backend/app/services/task_runtime.py backend/test/test_email_send_recovery.py
git commit -m "feat(email): recover interrupted sends without resending"
```

### 任务 7：状态门禁，禁止真实发送边界回退

**文件：**
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/test/test_batch_task_dispatch_schedule.py`
- 修改：`backend/test/test_concurrency_guards.py`

- [ ] **步骤 1：编写失败测试**

在 `backend/test/test_batch_task_dispatch_schedule.py` 增加测试：

```python
def test_approve_and_send_does_not_reset_sent_task(self) -> None:
    task_id = self._run_async(self._create_manual_approved_task())

    with patch(
        "app.services.task_runtime.mail_runtime.send_email",
        new=AsyncMock(return_value=self._build_send_result()),
    ) as mocked_send:
        self._run_async(dispatch_email_task(self.session_factory, task_id))
        with self.assertRaisesRegex(ValueError, "当前状态不能审核或发送"):
            self._run_async(
                approve_and_send_task(
                    self.session_factory,
                    task_id,
                    EmailTaskApprovalRequest(subject="再次发送", body_text="Body"),
                ),
            )

    self.assertEqual(mocked_send.await_count, 1)
    self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.SENT.value)


def test_approve_and_send_does_not_reset_confirming_task(self) -> None:
    task_id = self._run_async(self._create_manual_approved_task())
    self._run_async(self._set_task_status(task_id, EmailTaskStatus.SEND_CONFIRMING.value))

    with self.assertRaisesRegex(ValueError, "当前状态不能审核或发送"):
        self._run_async(
            approve_and_send_task(
                self.session_factory,
                task_id,
                EmailTaskApprovalRequest(subject="Hello", body_text="Body"),
            ),
        )
```

在 `backend/test/test_concurrency_guards.py` 增加状态门禁测试：

```python
def test_cancel_schedule_only_allows_scheduled_task(self) -> None:
    task_id = self._run_async(self._create_manual_draft_task())

    with self.assertRaisesRegex(ValueError, "当前状态不能取消定时"):
        self._run_async(cancel_scheduled_task(self.session_factory, task_id))
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_batch_task_dispatch_schedule backend.test.test_concurrency_guards -v
```

预期：
旧 `_ensure_task_allows_approval` 不拦 `sent/send_confirming`，测试失败。

- [ ] **步骤 3：实现状态门禁**

在 `task_runtime.py` 增加 allowed set：

```python
APPROVAL_ALLOWED_STATUSES = {
    EmailTaskStatus.REVIEW_REQUIRED.value,
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
    EmailTaskStatus.SEND_FAILED.value,
}

SCHEDULE_CANCEL_ALLOWED_STATUSES = {
    EmailTaskStatus.SCHEDULED.value,
}
```

修改 `_ensure_task_allows_approval`：

```python
def _ensure_task_allows_approval(task: EmailTask) -> None:
    if (
        task.status == EmailTaskStatus.CANCELED.value
        and task.cancellation_reason == EmailTaskCancellationReason.USER_REMOVED.value
    ):
        raise ValueError("该草稿已从批量任务中移除，不能再审核或发送")
    if task.status == EmailTaskStatus.GENERATING_DRAFT.value:
        raise ValueError("AI 正在改写当前草稿，请等待完成后再发送。")
    if task.status not in APPROVAL_ALLOWED_STATUSES:
        raise ValueError("当前状态不能审核或发送")
```

修改 `cancel_scheduled_task`：

```python
        if task.status not in SCHEDULE_CANCEL_ALLOWED_STATUSES:
            raise ValueError("当前状态不能取消定时")
```

保持 `DISPATCHABLE_EMAIL_TASK_STATUSES` 只包含 `approved/scheduled`：

```python
DISPATCHABLE_EMAIL_TASK_STATUSES = (
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
)
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_batch_task_dispatch_schedule backend.test.test_concurrency_guards -v
```

预期：
全部通过。

- [ ] **步骤 5：提交**

```bash
git add backend/app/services/task_runtime.py backend/test/test_batch_task_dispatch_schedule.py backend/test/test_concurrency_guards.py
git commit -m "fix(email): block send boundary state rollback"
```

### 任务 8：API 端接入幂等 key

**文件：**
- 修改：`backend/app/api/email_tasks.py`
- 修改：`backend/app/api/batch_tasks.py`
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写失败测试**

在 `backend/test/test_api_endpoints.py` 增加 API 级测试：

```python
def test_approve_and_send_reuses_idempotency_key(self) -> None:
    task_id = self._run_async(self._create_workspace_review_required_task())
    payload = {"subject": "Hello", "body_text": "Body", "body_html": None, "selected_material_ids": None}

    with patch(
        "app.services.task_runtime.mail_runtime.send_email",
        new=AsyncMock(return_value=self._build_send_result()),
    ) as mocked_send:
        first = self.client.post(
            f"/api/email-tasks/{task_id}/approve-and-send",
            json=payload,
            headers={"Idempotency-Key": "send-key-1"},
        )
        second = self.client.post(
            f"/api/email-tasks/{task_id}/approve-and-send",
            json=payload,
            headers={"Idempotency-Key": "send-key-1"},
        )

    self.assertEqual(first.status_code, 200)
    self.assertEqual(second.status_code, 200)
    self.assertEqual(mocked_send.await_count, 1)


def test_approve_and_send_rejects_same_key_with_different_body(self) -> None:
    task_id = self._run_async(self._create_workspace_review_required_task())

    with patch(
        "app.services.task_runtime.mail_runtime.send_email",
        new=AsyncMock(return_value=self._build_send_result()),
    ):
        first = self.client.post(
            f"/api/email-tasks/{task_id}/approve-and-send",
            json={"subject": "Hello", "body_text": "Body"},
            headers={"Idempotency-Key": "send-key-2"},
        )
    second = self.client.post(
        f"/api/email-tasks/{task_id}/approve-and-send",
        json={"subject": "Changed", "body_text": "Body"},
        headers={"Idempotency-Key": "send-key-2"},
    )

    self.assertEqual(first.status_code, 200)
    self.assertEqual(second.status_code, 409)
    self.assertIn("Idempotency-Key", second.json()["detail"])


def test_create_batch_task_reuses_idempotency_key(self) -> None:
    payload = self._build_create_batch_task_payload()

    first = self.client.post(
        "/api/batch-tasks",
        json=payload,
        headers={"Idempotency-Key": "batch-key-1"},
    )
    second = self.client.post(
        "/api/batch-tasks",
        json=payload,
        headers={"Idempotency-Key": "batch-key-1"},
    )

    self.assertEqual(first.status_code, 201)
    self.assertEqual(second.status_code, 200)
    self.assertEqual(first.json()["id"], second.json()["id"])
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_api_endpoints -v
```

预期：
重复 POST 会再次执行，或相同 key 没有冲突检查。

- [ ] **步骤 3：扩展 `approve_and_send_task` 和 `dispatch_email_task`**

修改 `approve_and_send_task` 签名：

```python
async def approve_and_send_task(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    payload: EmailTaskApprovalRequest,
    *,
    idempotency_key: str | None = None,
) -> tuple[int, int, int]:
```

调用 dispatch 时传入：

```python
    await dispatch_email_task(
        session_factory,
        task_id,
        respect_identity_send_window=False,
        idempotency_key=idempotency_key,
    )
```

- [ ] **步骤 4：在 API 层封装幂等执行**

在 `backend/app/api/email_tasks.py` import：

```python
from fastapi import Header
from fastapi.responses import JSONResponse
from app.api.workspace_support import build_workspace_thread_for_task
from app.models import IdempotencyRecord
from app.services.idempotency import (
    IdempotencyDecision,
    begin_idempotent_operation,
    complete_idempotency_record,
    fingerprint_idempotent_request,
    normalize_idempotency_key,
    new_worker_owner,
)
```

修改 `approve_and_send`：

```python
@router.post("/{task_id}/approve-and-send", response_model=WorkspaceThreadRead)
async def approve_and_send(
    task_id: int,
    payload: EmailTaskApprovalRequest,
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead | JSONResponse:
    idempotency_key = normalize_idempotency_key(idempotency_key_header)
    if idempotency_key is None:
        return await _run_workspace_action(
            session,
            lambda: approve_and_send_task(get_session_factory(), task_id, payload),
        )

    scope = f"POST /api/email-tasks/{task_id}/approve-and-send"
    fingerprint = fingerprint_idempotent_request(
        method="POST",
        path=f"/api/email-tasks/{task_id}/approve-and-send",
        body=payload.model_dump(mode="json"),
    )
    begin = await begin_idempotent_operation(
        session,
        scope=scope,
        key=idempotency_key,
        request_fingerprint=fingerprint,
        lease_owner=new_worker_owner("api-email-task"),
        now=utc_now(),
    )
    await session.commit()

    if begin.decision == IdempotencyDecision.CONFLICT:
        raise HTTPException(status_code=409, detail="Idempotency-Key 已用于不同请求内容")
    if begin.decision == IdempotencyDecision.RETURN_COMPLETED and begin.record.response_body is not None:
        return JSONResponse(status_code=begin.record.response_status_code or 200, content=begin.record.response_body)
    if begin.decision in {IdempotencyDecision.RETURN_IN_PROGRESS, IdempotencyDecision.RETURN_RECOVERING, IdempotencyDecision.RETURN_UNKNOWN}:
        session.expire_all()
        return await build_workspace_thread_for_task(session, task_id=task_id)

    response = await _run_workspace_action(
        session,
        lambda: approve_and_send_task(
            get_session_factory(),
            task_id,
            payload,
            idempotency_key=idempotency_key,
        ),
    )
    async with get_session_factory()() as write_session:
        record = await write_session.get(IdempotencyRecord, begin.record.id)
        assert record is not None
        await complete_idempotency_record(
            write_session,
            record,
            response_status_code=200,
            response_body=response.model_dump(mode="json"),
            result_entity_type="email_task",
            result_entity_id=str(task_id),
            now=utc_now(),
        )
        await write_session.commit()
    return response
```

同样处理 `backend/app/api/batch_tasks.py`：

- `create_batch_task`：scope 为 `POST /api/batch-tasks`，成功后保存 `BatchTaskCardRead.model_dump(mode="json")`。
- `approve_and_send_batch_task_item_draft`：scope 为 `POST /api/batch-tasks/{task_id}/items/{item_id}/approve-and-send`，传 key 到 `approve_and_send_task`。

- [ ] **步骤 5：运行测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_api_endpoints -v
```

预期：
新增 API 幂等测试通过。

- [ ] **步骤 6：提交**

```bash
git add backend/app/api/email_tasks.py backend/app/api/batch_tasks.py backend/app/services/task_runtime.py backend/test/test_api_endpoints.py
git commit -m "feat(api): enforce idempotency keys for send endpoints"
```

### 任务 9：前端生成幂等 key，并限制桌面端 POST 自动重试

**文件：**
- 创建：`frontend/src/lib/api/idempotency.ts`
- 修改：`frontend/src/lib/api/client.ts`
- 修改：`frontend/src/lib/api/emailTasksApi.ts`
- 修改：`frontend/src/lib/api/batchTasksApi.ts`
- 修改：`frontend/test/apiClient.test.ts`
- 修改：`frontend/test/BatchTasksApi.test.ts`
- 创建：`frontend/test/EmailTasksApi.test.ts`

- [ ] **步骤 1：编写失败测试**

在 `frontend/test/apiClient.test.ts` 增加：

```typescript
it("does not retry desktop POST without an idempotency key", async () => {
  window.autoEmailSender = {
    backendBaseUrl: "http://127.0.0.1:48120",
    getBackendBaseUrl: () => "http://127.0.0.1:48120",
    getVersion: async () => "0.1.0",
    checkForUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
    downloadUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
    switchToFullDownload: async () => ({ state: "not_available", version: "0.1.0" }),
    quitAndInstall: async () => undefined,
    onBackendStatus: () => () => undefined,
    onUpdateStatus: () => () => undefined,
  };
  updateDesktopBackendBaseUrl("http://127.0.0.1:48120");
  const networkError = new TypeError("Failed to fetch");
  const fetchMock = vi.spyOn(globalThis, "fetch").mockRejectedValue(networkError);

  await expect(apiFetch("/api/email-tasks/1/approve-and-send", { method: "POST" })).rejects.toBe(networkError);

  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("retries desktop POST when an idempotency key is present", async () => {
  let backendStatusCallback: ((status: DesktopBackendStatus) => void) | undefined;
  let backendBaseUrl = "http://127.0.0.1:48120";
  window.autoEmailSender = {
    backendBaseUrl,
    getBackendBaseUrl: () => backendBaseUrl,
    getVersion: async () => "0.1.0",
    checkForUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
    downloadUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
    switchToFullDownload: async () => ({ state: "not_available", version: "0.1.0" }),
    quitAndInstall: async () => undefined,
    onBackendStatus: (callback) => {
      backendStatusCallback = callback;
      return () => undefined;
    },
    onUpdateStatus: () => () => undefined,
  };
  updateDesktopBackendBaseUrl("http://127.0.0.1:48120");
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockRejectedValueOnce(new TypeError("Failed to fetch"))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "Content-Type": "application/json" } }));

  const request = apiFetch<{ ok: boolean }>("/api/email-tasks/1/approve-and-send", {
    method: "POST",
    headers: { "Idempotency-Key": "key-1" },
  });
  await Promise.resolve();
  backendBaseUrl = "http://127.0.0.1:48121";
  backendStatusCallback?.({
    state: "ready",
    baseUrl: backendBaseUrl,
    phase: "ready",
    message: "系统已准备就绪",
    elapsedSeconds: 1,
  });

  await expect(request).resolves.toEqual({ ok: true });
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
```

在 `frontend/test/BatchTasksApi.test.ts` 增加：

```typescript
it("sends an idempotency key when creating a batch task", async () => {
  mockedApiFetch.mockResolvedValue({});

  await createBatchTask({
    name: "Batch",
    identity_id: 1,
    llm_profile_id: 2,
    professor_ids: [3],
    schedule_type: "immediate",
    scheduled_dates: [],
    window_start_time: null,
    window_end_time: null,
    emails_per_window: null,
    primary_material_id: null,
    selected_material_ids: null,
    outreach_generation_mode: "template",
    outreach_template_subject: "Hello",
    outreach_template_body_text: "Body",
    outreach_template_body_html: null,
    email_subject: null,
    email_body: null,
  });

  expect(mockedApiFetch).toHaveBeenCalledWith(
    "/api/batch-tasks",
    expect.objectContaining({
      method: "POST",
      headers: expect.objectContaining({ "Idempotency-Key": expect.any(String) }),
    }),
  );
});
```

创建 `frontend/test/EmailTasksApi.test.ts`：

```typescript
import { beforeEach, describe, expect, it, vi } from "vitest";
import { approveAndSend } from "@/lib/api/emailTasksApi";

const mockedApiFetch = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api/client", () => ({
  apiFetch: mockedApiFetch,
}));

describe("emailTasksApi", () => {
  beforeEach(() => {
    mockedApiFetch.mockReset();
  });

  it("sends an idempotency key for approve and send", async () => {
    mockedApiFetch.mockResolvedValue({});

    await approveAndSend(7, {
      subject: "Hello",
      body_text: "Body",
      body_html: null,
      selected_material_ids: null,
    });

    expect(mockedApiFetch).toHaveBeenCalledWith(
      "/api/email-tasks/7/approve-and-send",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Idempotency-Key": expect.any(String) }),
      }),
    );
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend && npm run test -- apiClient.test.ts BatchTasksApi.test.ts EmailTasksApi.test.ts
```

预期：
新增测试失败，因为没有 key 工具，且 `apiFetch` 会重试所有桌面网络错误。

- [ ] **步骤 3：新增前端 key 工具**

创建 `frontend/src/lib/api/idempotency.ts`：

```typescript
export const IDEMPOTENCY_KEY_HEADER = "Idempotency-Key";

export const createIdempotencyKey = (scope: string): string => {
  const randomPart =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${scope}:${randomPart}`;
};

export const withIdempotencyKey = (options: RequestInit, scope: string): RequestInit => {
  const headers = new Headers(options.headers ?? {});
  if (!headers.has(IDEMPOTENCY_KEY_HEADER)) {
    headers.set(IDEMPOTENCY_KEY_HEADER, createIdempotencyKey(scope));
  }
  return {
    ...options,
    headers,
  };
};
```

- [ ] **步骤 4：限制 `apiFetch` 重试**

在 `frontend/src/lib/api/client.ts` 增加：

```typescript
const SAFE_RETRY_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

function requestCanBeRetried(method: string, options?: RequestInit): boolean {
  if (SAFE_RETRY_METHODS.has(method)) {
    return true;
  }
  const headers = new Headers(options?.headers ?? {});
  return headers.has("Idempotency-Key");
}
```

修改重试判断：

```typescript
    if (shouldRetryDesktopNetworkError(error) && requestCanBeRetried(method, options)) {
```

- [ ] **步骤 5：给关键 API wrapper 加 key**

修改 `frontend/src/lib/api/emailTasksApi.ts`：

```typescript
import { withIdempotencyKey } from '@/lib/api/idempotency';

export const approveAndSend = (taskId: number, payload: EmailTaskApprovalPayloadDTO) =>
  apiFetch<WorkspaceThreadDTO>(
    `/api/email-tasks/${taskId}/approve-and-send`,
    withIdempotencyKey(
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
      `email-task:${taskId}:approve-and-send`,
    ),
  );
```

修改 `frontend/src/lib/api/batchTasksApi.ts`：

```typescript
import { withIdempotencyKey } from '@/lib/api/idempotency';

export const createBatchTask = (payload: CreateBatchTaskRequestDTO) =>
  apiFetch<BatchTaskCardDTO>(
    '/api/batch-tasks',
    withIdempotencyKey(
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
      'batch-task:create',
    ),
  );

export const approveAndSendBatchTaskItemDraft = (
  taskId: number,
  itemId: number,
  payload: EmailTaskApprovalPayloadDTO,
) =>
  apiFetch<WorkspaceThreadDTO>(
    `/api/batch-tasks/${taskId}/items/${itemId}/approve-and-send`,
    withIdempotencyKey(
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
      `batch-task:${taskId}:item:${itemId}:approve-and-send`,
    ),
  );
```

- [ ] **步骤 6：运行测试验证通过**

运行：

```bash
cd frontend && npm run test -- apiClient.test.ts BatchTasksApi.test.ts EmailTasksApi.test.ts
```

预期：
全部通过。

- [ ] **步骤 7：提交**

```bash
git add frontend/src/lib/api/idempotency.ts frontend/src/lib/api/client.ts frontend/src/lib/api/emailTasksApi.ts frontend/src/lib/api/batchTasksApi.ts frontend/test/apiClient.test.ts frontend/test/BatchTasksApi.test.ts frontend/test/EmailTasksApi.test.ts
git commit -m "feat(frontend): send idempotency keys for email writes"
```

### 任务 10：状态展示、批量 item 行为和重发上下文

**文件：**
- 修改：`backend/app/services/batch_task_item_actions.py`
- 修改：`backend/app/services/batch_task_resend_context.py`
- 修改：`backend/app/schemas/batch_task.py`
- 修改：`backend/app/api/batch_tasks.py`
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/features/workspace/client/getWorkspaceNextStep.ts`
- 创建或修改：`frontend/src/features/workspace/client/getWorkspaceNextStep.test.ts`

- [ ] **步骤 1：编写失败测试**

在 `frontend/src/features/workspace/client/getWorkspaceNextStep.test.ts` 增加：

```typescript
import { describe, expect, it } from "vitest";
import { getWorkspaceNextStep } from "./getWorkspaceNextStep";
import type { WorkspaceTaskSummaryDTO } from "@/types";

const buildTask = (status: WorkspaceTaskSummaryDTO["status"]): WorkspaceTaskSummaryDTO => ({
  id: 1,
  source: "manual",
  batch_task_id: null,
  parent_task_id: null,
  status,
  cancellation_reason: null,
  can_continue_manually: false,
  can_write_follow_up: false,
  outreach_generation_mode: "template",
  outreach_template_subject: null,
  outreach_template_body_text: null,
  outreach_template_body_html: null,
  rendered_template_subject: null,
  rendered_template_body_text: null,
  rendered_template_body_html: null,
  match_score: null,
  match_reason: null,
  fit_points: [],
  risk_points: [],
  match_keywords: [],
  generated_subject: null,
  generated_content_text: null,
  generated_content_html: null,
  approved_subject: null,
  approved_body_text: null,
  approved_body_html: null,
  primary_material_id: null,
  primary_material: null,
  selected_material_ids: null,
  approved_at: null,
  scheduled_at: null,
  last_send_attempt_at: null,
  sent_at: null,
  last_rfc_message_id: null,
  retry_count: 0,
  last_error: null,
  is_replied: false,
  estimated_prompt_tokens: null,
  estimated_completion_tokens_upper_bound: null,
  estimated_total_tokens_upper_bound: null,
  last_draft_prompt_tokens: null,
  last_draft_completion_tokens: null,
  last_draft_total_tokens: null,
  draft: {
    subject: null,
    body_text: "",
    body_html: null,
    source: "manual_empty",
    sendable: false,
    editable: false,
  },
});

describe("getWorkspaceNextStep send recovery statuses", () => {
  it("explains send confirming without enabling resend", () => {
    const step = getWorkspaceNextStep(buildTask("send_confirming"));
    expect(step.title).toContain("确认");
    expect(step.primaryAction).toBeNull();
  });

  it("explains send unconfirmed without enabling automatic resend", () => {
    const step = getWorkspaceNextStep(buildTask("send_unconfirmed"));
    expect(step.title).toContain("未确认");
    expect(step.primaryAction).toBeNull();
  });
});
```

在后端为 `batch_task_resend_context` 增加测试：

```python
def test_send_unconfirmed_item_is_not_default_resend_candidate(self) -> None:
    task = EmailTask(status=EmailTaskStatus.SEND_UNCONFIRMED.value)
    task.professor = Professor(name="老师", email="teacher@example.edu")

    decision = decide_resend_item(task)

    self.assertFalse(decision.selectable)
    self.assertEqual(decision.reason_label, "发送状态未确认")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend && npm run test -- getWorkspaceNextStep.test.ts
cd backend && uv run python -m unittest backend.test.test_batch_task_dispatch_schedule -v
```

预期：
前端类型不接受新状态，后端重发上下文没有新状态语义。

- [ ] **步骤 3：更新后端状态行为**

修改 `backend/app/services/batch_task_item_actions.py`：

```python
    if email_task.status == EmailTaskStatus.SEND_CONFIRMING.value:
        return None
    if email_task.status == EmailTaskStatus.SEND_UNCONFIRMED.value:
        return None
```

修改 `backend/app/services/batch_task_resend_context.py`：

```python
EXCLUDED_RUNNING_STATUSES = {
    EmailTaskStatus.SENDING.value,
    EmailTaskStatus.SEND_CONFIRMING.value,
    EmailTaskStatus.SEND_UNCONFIRMED.value,
}

REASON_LABELS: dict[tuple[str, str | None], str] = {
    (EmailTaskStatus.SEND_CONFIRMING.value, None): "发送确认中",
    (EmailTaskStatus.SEND_UNCONFIRMED.value, None): "发送状态未确认",
}
```

在 `decide_resend_item` 中给排除状态返回更准确文案：

```python
    if email_task.status in EXCLUDED_RUNNING_STATUSES:
        reason_label = REASON_LABELS.get((email_task.status, None), "发送中")
        return ResendItemDecision(False, False, reason_label, "发送结果尚未确认，未带入新任务")
```

修改 `backend/app/api/batch_tasks.py` 的 `_serialize_batch_task` 计数逻辑，把：

```python
failed_count = counts.get(EmailTaskStatus.SEND_FAILED.value, 0)
```

改为：

```python
failed_count = counts.get(EmailTaskStatus.SEND_FAILED.value, 0)
confirming_count = counts.get(EmailTaskStatus.SEND_CONFIRMING.value, 0)
unconfirmed_count = counts.get(EmailTaskStatus.SEND_UNCONFIRMED.value, 0)
```

并在 `BatchTaskCardRead` 加字段 `send_confirming_count`、`send_unconfirmed_count`。

- [ ] **步骤 4：更新前端类型和文案**

修改 `frontend/src/types/index.ts`：

```typescript
export type WorkspaceTaskStatus =
  | 'discovered'
  | 'matched'
  | 'generating_draft'
  | 'draft_failed'
  | 'review_required'
  | 'approved'
  | 'scheduled'
  | 'sending'
  | 'send_confirming'
  | 'send_unconfirmed'
  | 'sent'
  | 'send_failed'
  | 'reply_detected'
  | 'canceled';
```

更新 label：

```typescript
send_confirming: '确认发送中',
send_unconfirmed: '发送状态未确认',
```

修改 `frontend/src/features/workspace/client/getWorkspaceNextStep.ts`：

```typescript
if (task.status === "send_confirming") {
  return {
    title: "正在确认发送结果",
    description: "系统正在核验服务商是否已经接收这封邮件，不会自动重复发送。",
    primaryAction: null,
  };
}

if (task.status === "send_unconfirmed") {
  return {
    title: "发送状态未确认",
    description: "系统没有足够证据判断这封邮件是否已成功投递，因此不会自动重复发送。",
    primaryAction: null,
  };
}
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```bash
cd frontend && npm run test -- getWorkspaceNextStep.test.ts
cd backend && uv run python -m unittest backend.test.test_batch_task_dispatch_schedule -v
```

预期：
全部通过。

- [ ] **步骤 6：提交**

```bash
git add backend/app/services/batch_task_item_actions.py backend/app/services/batch_task_resend_context.py backend/app/schemas/batch_task.py backend/app/api/batch_tasks.py frontend/src/types/index.ts frontend/src/features/workspace/client/getWorkspaceNextStep.ts frontend/src/features/workspace/client/getWorkspaceNextStep.test.ts
git commit -m "feat(email): surface send confirmation states"
```

### 任务 11：全量回归和迁移验证

**文件：**
- 修改：`docs/superpowers/specs/2026-07-07-email-send-idempotency-design.md`

- [ ] **步骤 1：运行后端重点测试**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_email_send_idempotency_models backend.test.test_idempotency_service backend.test.test_email_send_attempts backend.test.test_email_send_recovery backend.test.test_batch_task_dispatch_schedule backend.test.test_concurrency_guards backend.test.test_mail_runtime backend.test.test_api_endpoints backend.test.test_database_schema -v
```

预期：
全部通过。

- [ ] **步骤 2：运行后端全量测试**

运行：

```bash
cd backend && uv run python -m unittest discover test
```

预期：
全部通过。

- [ ] **步骤 3：运行前端重点测试**

运行：

```bash
cd frontend && npm run test -- apiClient.test.ts BatchTasksApi.test.ts EmailTasksApi.test.ts getWorkspaceNextStep.test.ts
```

预期：
全部通过。

- [ ] **步骤 4：运行前端 lint 和全量测试**

运行：

```bash
cd frontend && npm run lint
cd frontend && npm run test
```

预期：
lint 和测试全部通过。

- [ ] **步骤 5：运行迁移验证**

运行：

```bash
cd backend && uv run python -m alembic upgrade head
cd backend && uv run python -m unittest backend.test.test_migrated_database backend.test.test_database_schema -v
```

预期：
Alembic 能升级到 `20260707_send_idempotency`，迁移数据库和 schema 测试通过。

- [ ] **步骤 6：更新规格文档实现状态**

在 `docs/superpowers/specs/2026-07-07-email-send-idempotency-design.md` 末尾追加：

```markdown
## 实现状态

- 已实现请求幂等记录，覆盖工作区立即发送、批量 item 立即发送、创建批量任务。
- 已实现发送 attempt 持久化，SMTP 前预生成 Message-ID。
- 已实现 SMTP 成功后先落库 sent，再执行已发送箱同步。
- 已实现中断恢复核验，SMTP 风险区任务不会自动恢复为可发送。
- 已实现前端 `Idempotency-Key` 和非幂等 POST 重试限制。
```

- [ ] **步骤 7：最终提交**

```bash
git add docs/superpowers/specs/2026-07-07-email-send-idempotency-design.md
git commit -m "docs: record email send idempotency implementation"
```
