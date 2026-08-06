# 邮箱历史自动同步与统一通信记录实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 默认自动同步系统已有老师相关的 INBOX 和 Sent 邮件，统一入库、统一展示、统一统计，并用多层去重避免重复通信记录。

**架构：** 以 `email_logs` 作为统一通信记录表，新增内部 IMAP 元数据和去重键；IMAP 同步分为文件夹级增量游标与老师级历史补扫；工作区、老师状态和统计面板统一从通信记录读取真实联系状态。用户侧不展示来源区别，系统侧保留 `ingest_source`、文件夹、UID 和指纹信息。

**技术栈：** FastAPI、SQLAlchemy 2.x、Alembic、SQLite、Python `imaplib`、unittest、React/Vite 前端现有类型。

---

## 规格来源

- 规格文档：`docs/superpowers/specs/2026-06-30-unified-email-history-sync-design.md`
- 现有相关实现：
  - `backend/app/models/email_log.py`
  - `backend/app/models/imap_sync.py`
  - `backend/app/services/mail_runtime.py`
  - `backend/app/services/imap_message_fetcher.py`
  - `backend/app/services/imap_sync_state.py`
  - `backend/app/services/task_runtime.py`
  - `backend/app/services/contact_status.py`
  - `backend/app/services/dashboard_stats.py`
  - `backend/app/api/workspace_support.py`

## 文件结构

- 修改：`backend/app/models/email_log.py`
  - 让 `llm_profile_id` 可空。
  - 增加统一通信记录内部字段：收发件人、IMAP 定位、规范化 Message-ID、内容指纹、同步时间。
  - 删除全局 `rfc_message_id` 唯一约束，改用局部唯一索引。

- 修改：`backend/app/models/imap_sync.py`
  - 为邮箱和老师扫描状态增加 `folder_role`。
  - 保留 `folder` 作为真实 IMAP 文件夹名，默认 `INBOX`。

- 创建：`backend/alembic/versions/20260630_unified_email_history_sync.py`
  - 迁移 `email_logs`、`imap_mailbox_sync_states`、`imap_professor_sync_states`。
  - 调整唯一索引，兼容已有数据。

- 创建：`backend/app/services/email_addresses.py`
  - 负责邮箱地址规范化、头部邮箱列表解析、列表比较。

- 创建：`backend/app/services/email_log_ingestion.py`
  - 统一通信记录入库入口。
  - 实现 Message-ID、IMAP 定位、内容指纹三层去重。
  - 实现字段补齐、不覆盖非空正文。

- 修改：`backend/app/services/imap_message_fetcher.py`
  - `ImapFetchedMessage` 增加 `to_emails`、`cc_emails`、`bcc_emails`、`raw_from`、`raw_to`、`raw_cc`、`raw_bcc`。
  - 增加 `search_uids_to_recipient`、`search_uids_cc_recipient`。

- 修改：`backend/app/services/mail_runtime.py`
  - 增加 Sent 文件夹发现。
  - 增加按任意文件夹增量拉取和按老师邮箱历史搜索。
  - 旧 `fetch_incremental_inbox_messages`、`fetch_professor_history_inbox_messages` 保留为兼容包装。

- 修改：`backend/app/services/imap_sync_state.py`
  - 为所有系统已有且未归档、有邮箱的老师创建 `inbox` 和 `sent` 历史扫描状态。
  - 领取历史扫描任务时支持文件夹角色。

- 修改：`backend/app/services/task_runtime.py`
  - `sync_identity_imap_once` 同步 `inbox` 与 `sent`。
  - 增量同步使用文件夹级游标。
  - 历史补扫根据状态的 `folder_role` 决定搜索方向。
  - 同步命中的邮件通过 `email_log_ingestion` 入库并修正任务状态。

- 修改：`backend/app/services/contact_status.py`
  - 老师状态从统一通信记录计算，收到邮件也能使老师进入已回复状态。

- 修改：`backend/app/services/dashboard_stats.py`
  - 发送数、已联系导师数、回复数、回复率基于统一通信记录。
  - `contacted_professor_ids` 使用发送或收到通信记录的老师集合，避免收到邮件但分母为 0。

- 修改：`backend/app/api/workspace_support.py`
  - 工作区消息时间线按 `identity_id + professor_id` 查询统一通信记录。
  - 继续只把当前任务草稿显示在消息列表中。

- 修改测试：
  - `backend/test/test_unified_email_log_models.py`
  - `backend/test/test_email_log_ingestion.py`
  - `backend/test/test_mail_runtime.py`
  - `backend/test/test_imap_sync_models.py`
  - `backend/test/test_imap_sync_runtime.py`
  - `backend/test/test_contact_status.py`
  - `backend/test/test_dashboard_stats.py`
  - `backend/test/test_workspace_support.py`

## 任务 1：数据模型、迁移和唯一约束

**文件：**
- 修改：`backend/app/models/email_log.py`
- 修改：`backend/app/models/imap_sync.py`
- 创建：`backend/alembic/versions/20260630_unified_email_history_sync.py`
- 创建：`backend/test/test_unified_email_log_models.py`
- 修改：`backend/test/test_imap_sync_models.py`

- [ ] **步骤 1：编写失败的模型测试**

创建 `backend/test/test_unified_email_log_models.py`：

```python
from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, EmailDirection, EmailLog, IdentityProfile, Professor


class UnifiedEmailLogModelTestCase(unittest.TestCase):
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

    def test_imap_email_log_can_exist_without_llm_profile(self) -> None:
        async def scenario() -> tuple[str, str, int]:
            async with self.session_factory() as session:
                identity = IdentityProfile(
                    name="身份",
                    profile_name="身份",
                    sender_name="学生",
                    email_address="student@example.com",
                    smtp_host="smtp.example.com",
                    smtp_port=465,
                    smtp_username="student@example.com",
                    smtp_password="secret",
                )
                professor = Professor(name="老师", email="teacher@example.edu")
                session.add_all([identity, professor])
                await session.flush()
                session.add(
                    EmailLog(
                        email_task_id=None,
                        identity_id=identity.id,
                        llm_profile_id=None,
                        professor_id=professor.id,
                        direction=EmailDirection.SENT.value,
                        subject="Hello",
                        content="Body",
                        rfc_message_id="<message@example.com>",
                        normalized_message_id="<message@example.com>",
                        ingest_source="imap",
                        folder_role="sent",
                        folder="Sent",
                        uidvalidity=123,
                        imap_uid=456,
                        from_email="student@example.com",
                        to_emails=["teacher@example.edu"],
                        cc_emails=[],
                        bcc_emails=[],
                        message_fingerprint="fp-1",
                        synced_at=datetime(2026, 6, 30, tzinfo=UTC),
                    )
                )
                await session.commit()

            async with self.session_factory() as session:
                log = (await session.scalars(select(EmailLog))).one()
                return log.ingest_source, log.folder_role, log.imap_uid or 0

        self.assertEqual(self._run_async(scenario()), ("imap", "sent", 456))

    def test_duplicate_message_id_is_allowed_for_different_professors(self) -> None:
        async def scenario() -> int:
            async with self.session_factory() as session:
                identity = IdentityProfile(
                    name="身份",
                    profile_name="身份",
                    sender_name="学生",
                    email_address="student@example.com",
                    smtp_host="smtp.example.com",
                    smtp_port=465,
                    smtp_username="student@example.com",
                    smtp_password="secret",
                )
                left = Professor(name="左老师", email="left@example.edu")
                right = Professor(name="右老师", email="right@example.edu")
                session.add_all([identity, left, right])
                await session.flush()
                for professor in [left, right]:
                    session.add(
                        EmailLog(
                            identity_id=identity.id,
                            llm_profile_id=None,
                            professor_id=professor.id,
                            direction=EmailDirection.SENT.value,
                            subject="Hello",
                            content="Body",
                            rfc_message_id="<shared@example.com>",
                            normalized_message_id="<shared@example.com>",
                            ingest_source="imap",
                        )
                    )
                await session.commit()
                return len(list(await session.scalars(select(EmailLog))))

        self.assertEqual(self._run_async(scenario()), 2)

    def test_duplicate_message_id_is_rejected_for_same_professor_direction(self) -> None:
        async def scenario() -> None:
            async with self.session_factory() as session:
                identity = IdentityProfile(
                    name="身份",
                    profile_name="身份",
                    sender_name="学生",
                    email_address="student@example.com",
                    smtp_host="smtp.example.com",
                    smtp_port=465,
                    smtp_username="student@example.com",
                    smtp_password="secret",
                )
                professor = Professor(name="老师", email="teacher@example.edu")
                session.add_all([identity, professor])
                await session.flush()
                for index in range(2):
                    session.add(
                        EmailLog(
                            identity_id=identity.id,
                            llm_profile_id=None,
                            professor_id=professor.id,
                            direction=EmailDirection.SENT.value,
                            subject=f"Hello {index}",
                            content="Body",
                            rfc_message_id="<same@example.com>",
                            normalized_message_id="<same@example.com>",
                            ingest_source="imap",
                        )
                    )
                await session.commit()

        with self.assertRaises(IntegrityError):
            self._run_async(scenario())
```

修改 `backend/test/test_imap_sync_models.py`，新增断言：

```python
def test_mailbox_state_tracks_folder_role(self) -> None:
    async def scenario() -> tuple[str, str]:
        async with self.session_factory() as session:
            session.add(ImapMailboxSyncState(identity_id=1, folder_role="sent", folder="Sent"))
            await session.commit()
            saved = await session.scalar(select(ImapMailboxSyncState))
            return saved.folder_role, saved.folder

    self.assertEqual(self._run_async(scenario()), ("sent", "Sent"))
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest test.test_unified_email_log_models test.test_imap_sync_models
```

预期：

- `test_imap_email_log_can_exist_without_llm_profile` 失败，原因是 `llm_profile_id` 不可为空或新字段不存在。
- `test_mailbox_state_tracks_folder_role` 失败，原因是 `folder_role` 不存在。

- [ ] **步骤 3：修改模型**

在 `backend/app/models/email_log.py` 中：

```python
from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, text


class EmailLog(Base):
    __tablename__ = "email_logs"
    __table_args__ = (
        Index(
            "uq_email_logs_identity_professor_direction_message",
            "identity_id",
            "professor_id",
            "direction",
            "normalized_message_id",
            unique=True,
            sqlite_where=text("normalized_message_id IS NOT NULL"),
        ),
        Index(
            "uq_email_logs_identity_professor_imap_uid",
            "identity_id",
            "professor_id",
            "folder_role",
            "folder",
            "uidvalidity",
            "imap_uid",
            unique=True,
            sqlite_where=text(
                "folder_role IS NOT NULL AND folder IS NOT NULL "
                "AND uidvalidity IS NOT NULL AND imap_uid IS NOT NULL"
            ),
        ),
        Index(
            "uq_email_logs_identity_professor_direction_fingerprint",
            "identity_id",
            "professor_id",
            "direction",
            "message_fingerprint",
            unique=True,
            sqlite_where=text("message_fingerprint IS NOT NULL"),
        ),
    )

    llm_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_profiles.id"),
        index=True,
        nullable=True,
    )
    ingest_source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'system'"),
    )
    folder_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    folder: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uidvalidity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imap_uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    normalized_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_emails: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    cc_emails: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    bcc_emails: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
```

在 `backend/app/models/imap_sync.py` 中：

```python
class ImapFolderRole(str, Enum):
    INBOX = "inbox"
    SENT = "sent"


class ImapMailboxSyncState(Base):
    __table_args__ = (
        UniqueConstraint(
            "identity_id",
            "folder_role",
            "folder",
            name="uq_imap_mailbox_identity_role_folder",
        ),
    )
    folder_role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'inbox'"),
    )
    folder: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        server_default=text("'INBOX'"),
    )


class ImapProfessorSyncState(Base):
    __table_args__ = (
        UniqueConstraint(
            "identity_id",
            "professor_id",
            "professor_email",
            "folder_role",
            "folder",
            name="uq_imap_professor_identity_professor_email_role_folder",
        ),
    )
    folder_role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'inbox'"),
    )
    folder: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        server_default=text("'INBOX'"),
    )
```

在 `backend/app/models/__init__.py` 导出 `ImapFolderRole`。

- [ ] **步骤 4：新增 Alembic 迁移**

创建 `backend/alembic/versions/20260630_unified_email_history_sync.py`：

```python
"""unified email history sync

Revision ID: 20260630unimapsync
Revises: 20260614taskmat
Create Date: 2026-06-30 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260630unimapsync"
down_revision: Union[str, Sequence[str], None] = "20260614taskmat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("email_logs") as batch_op:
        batch_op.drop_constraint("uq_email_logs_rfc_message_id", type_="unique")
        batch_op.alter_column("llm_profile_id", existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(sa.Column("ingest_source", sa.String(length=20), server_default=sa.text("'system'"), nullable=False))
        batch_op.add_column(sa.Column("folder_role", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("folder", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("uidvalidity", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("imap_uid", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("normalized_message_id", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("message_fingerprint", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("from_email", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("to_emails", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("cc_emails", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("bcc_emails", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(
        "UPDATE email_logs SET normalized_message_id = lower(trim(rfc_message_id)) "
        "WHERE rfc_message_id IS NOT NULL AND trim(rfc_message_id) != ''"
    )
    op.create_index(
        "uq_email_logs_identity_professor_direction_message",
        "email_logs",
        ["identity_id", "professor_id", "direction", "normalized_message_id"],
        unique=True,
        sqlite_where=sa.text("normalized_message_id IS NOT NULL"),
    )
    op.create_index(
        "uq_email_logs_identity_professor_imap_uid",
        "email_logs",
        ["identity_id", "professor_id", "folder_role", "folder", "uidvalidity", "imap_uid"],
        unique=True,
        sqlite_where=sa.text(
            "folder_role IS NOT NULL AND folder IS NOT NULL "
            "AND uidvalidity IS NOT NULL AND imap_uid IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_email_logs_identity_professor_direction_fingerprint",
        "email_logs",
        ["identity_id", "professor_id", "direction", "message_fingerprint"],
        unique=True,
        sqlite_where=sa.text("message_fingerprint IS NOT NULL"),
    )

    with op.batch_alter_table("imap_mailbox_sync_states") as batch_op:
        batch_op.drop_constraint("uq_imap_mailbox_identity_folder", type_="unique")
        batch_op.add_column(sa.Column("folder_role", sa.String(length=20), server_default=sa.text("'inbox'"), nullable=False))
        batch_op.alter_column("folder", existing_type=sa.String(length=64), type_=sa.String(length=255), existing_nullable=False)
        batch_op.create_unique_constraint(
            "uq_imap_mailbox_identity_role_folder",
            ["identity_id", "folder_role", "folder"],
        )

    with op.batch_alter_table("imap_professor_sync_states") as batch_op:
        batch_op.drop_constraint("uq_imap_professor_identity_professor_email_folder", type_="unique")
        batch_op.add_column(sa.Column("folder_role", sa.String(length=20), server_default=sa.text("'inbox'"), nullable=False))
        batch_op.alter_column("folder", existing_type=sa.String(length=64), type_=sa.String(length=255), existing_nullable=False)
        batch_op.create_unique_constraint(
            "uq_imap_professor_identity_professor_email_role_folder",
            ["identity_id", "professor_id", "professor_email", "folder_role", "folder"],
        )


def downgrade() -> None:
    op.drop_index("uq_email_logs_identity_professor_direction_fingerprint", table_name="email_logs")
    op.drop_index("uq_email_logs_identity_professor_imap_uid", table_name="email_logs")
    op.drop_index("uq_email_logs_identity_professor_direction_message", table_name="email_logs")

    with op.batch_alter_table("imap_professor_sync_states") as batch_op:
        batch_op.drop_constraint("uq_imap_professor_identity_professor_email_role_folder", type_="unique")
        batch_op.drop_column("folder_role")
        batch_op.create_unique_constraint(
            "uq_imap_professor_identity_professor_email_folder",
            ["identity_id", "professor_id", "professor_email", "folder"],
        )

    with op.batch_alter_table("imap_mailbox_sync_states") as batch_op:
        batch_op.drop_constraint("uq_imap_mailbox_identity_role_folder", type_="unique")
        batch_op.drop_column("folder_role")
        batch_op.create_unique_constraint("uq_imap_mailbox_identity_folder", ["identity_id", "folder"])

    with op.batch_alter_table("email_logs") as batch_op:
        batch_op.drop_column("synced_at")
        batch_op.drop_column("bcc_emails")
        batch_op.drop_column("cc_emails")
        batch_op.drop_column("to_emails")
        batch_op.drop_column("from_email")
        batch_op.drop_column("message_fingerprint")
        batch_op.drop_column("normalized_message_id")
        batch_op.drop_column("imap_uid")
        batch_op.drop_column("uidvalidity")
        batch_op.drop_column("folder")
        batch_op.drop_column("folder_role")
        batch_op.drop_column("ingest_source")
        batch_op.alter_column("llm_profile_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_unique_constraint("uq_email_logs_rfc_message_id", ["rfc_message_id"])
```

- [ ] **步骤 5：运行模型和迁移测试**

运行：

```bash
cd backend && uv run python -m unittest test.test_unified_email_log_models test.test_imap_sync_models
cd backend && uv run alembic upgrade head
```

预期：

- unittest 输出 `OK`。
- Alembic 升级无异常。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/models/email_log.py backend/app/models/imap_sync.py backend/app/models/__init__.py backend/alembic/versions/20260630_unified_email_history_sync.py backend/test/test_unified_email_log_models.py backend/test/test_imap_sync_models.py
git commit -m "feat(backend): add unified email log metadata"
```

## 任务 2：邮箱地址规范化和统一通信记录入库

**文件：**
- 创建：`backend/app/services/email_addresses.py`
- 创建：`backend/app/services/email_log_ingestion.py`
- 创建：`backend/test/test_email_log_ingestion.py`

- [ ] **步骤 1：编写失败测试**

创建 `backend/test/test_email_log_ingestion.py`：

```python
from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, EmailDirection, EmailLog, IdentityProfile, Professor
from app.services.email_log_ingestion import EmailLogIngestRecord, upsert_email_log


class EmailLogIngestionTestCase(unittest.TestCase):
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

    async def _seed_identity_and_professor(self) -> tuple[int, int]:
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="身份",
                profile_name="身份",
                sender_name="学生",
                email_address="student@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="student@example.com",
                smtp_password="secret",
            )
            professor = Professor(name="老师", email="teacher@example.edu")
            session.add_all([identity, professor])
            await session.commit()
            return identity.id, professor.id

    def test_upsert_merges_sent_folder_copy_by_message_id(self) -> None:
        async def scenario() -> tuple[int, str | None, int | None]:
            identity_id, professor_id = await self._seed_identity_and_professor()
            async with self.session_factory() as session:
                session.add(
                    EmailLog(
                        identity_id=identity_id,
                        llm_profile_id=None,
                        professor_id=professor_id,
                        direction=EmailDirection.SENT.value,
                        subject="Hello",
                        content="original body",
                        rfc_message_id="<shared@example.com>",
                        normalized_message_id="<shared@example.com>",
                        ingest_source="system",
                    )
                )
                await session.commit()

            async with self.session_factory() as session:
                log = await upsert_email_log(
                    session,
                    EmailLogIngestRecord(
                        identity_id=identity_id,
                        professor_id=professor_id,
                        direction=EmailDirection.SENT.value,
                        subject="Hello from sent",
                        content="sent folder body",
                        content_html="<p>sent folder body</p>",
                        message_id="<shared@example.com>",
                        from_email="student@example.com",
                        to_emails=["teacher@example.edu"],
                        cc_emails=[],
                        bcc_emails=[],
                        created_at=datetime(2026, 6, 30, 9, tzinfo=UTC),
                        ingest_source="imap",
                        folder_role="sent",
                        folder="Sent",
                        uidvalidity=88,
                        imap_uid=99,
                    ),
                )
                await session.commit()
                count = len(list(await session.scalars(select(EmailLog))))
                return count, log.folder, log.imap_uid

        self.assertEqual(self._run_async(scenario()), (1, "Sent", 99))

    def test_upsert_creates_unbound_sent_log_for_teacher(self) -> None:
        async def scenario() -> tuple[str, list[str]]:
            identity_id, professor_id = await self._seed_identity_and_professor()
            async with self.session_factory() as session:
                log = await upsert_email_log(
                    session,
                    EmailLogIngestRecord(
                        identity_id=identity_id,
                        professor_id=professor_id,
                        direction=EmailDirection.SENT.value,
                        subject="External hello",
                        content="Body",
                        content_html=None,
                        message_id="<external@example.com>",
                        from_email="Student <student@example.com>",
                        to_emails=["Teacher <teacher@example.edu>"],
                        cc_emails=[],
                        bcc_emails=[],
                        created_at=datetime(2026, 6, 30, 10, tzinfo=UTC),
                        ingest_source="imap",
                        folder_role="sent",
                        folder="Sent",
                        uidvalidity=1,
                        imap_uid=2,
                    ),
                )
                await session.commit()
                return log.normalized_message_id or "", log.to_emails or []

        self.assertEqual(
            self._run_async(scenario()),
            ("<external@example.com>", ["teacher@example.edu"]),
        )

    def test_upsert_uses_fingerprint_when_message_id_is_missing(self) -> None:
        async def scenario() -> int:
            identity_id, professor_id = await self._seed_identity_and_professor()
            record = EmailLogIngestRecord(
                identity_id=identity_id,
                professor_id=professor_id,
                direction=EmailDirection.RECEIVED.value,
                subject="Re: Hello",
                content="Reply",
                content_html=None,
                message_id=None,
                from_email="teacher@example.edu",
                to_emails=["student@example.com"],
                cc_emails=[],
                bcc_emails=[],
                created_at=datetime(2026, 6, 30, 11, 3, 12, tzinfo=UTC),
                ingest_source="imap",
                folder_role="inbox",
                folder="INBOX",
                uidvalidity=None,
                imap_uid=None,
            )
            async with self.session_factory() as session:
                await upsert_email_log(session, record)
                await upsert_email_log(session, record)
                await session.commit()
                return len(list(await session.scalars(select(EmailLog))))

        self.assertEqual(self._run_async(scenario()), 1)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest test.test_email_log_ingestion
```

预期：FAIL，报错 `No module named app.services.email_log_ingestion`。

- [ ] **步骤 3：实现邮箱地址工具**

创建 `backend/app/services/email_addresses.py`：

```python
from __future__ import annotations

from email.utils import getaddresses, parseaddr


def normalize_email_address(value: str | None) -> str:
    parsed = parseaddr(value or "")[1] or value or ""
    return parsed.strip().lower()


def normalize_email_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    if not values:
        return []
    addresses = getaddresses([str(value) for value in values])
    normalized: list[str] = []
    seen: set[str] = set()
    for _, address in addresses:
        email = normalize_email_address(address)
        if not email or email in seen:
            continue
        seen.add(email)
        normalized.append(email)
    return normalized


def email_matches(value: str | None, candidates: set[str]) -> bool:
    normalized = normalize_email_address(value)
    return bool(normalized and normalized in candidates)
```

- [ ] **步骤 4：实现统一入库服务**

创建 `backend/app/services/email_log_ingestion.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models import EmailLog
from app.services.email_addresses import normalize_email_address, normalize_email_list


@dataclass(frozen=True, slots=True)
class EmailLogIngestRecord:
    identity_id: int
    professor_id: int
    direction: str
    subject: str | None
    content: str
    content_html: str | None
    message_id: str | None
    from_email: str | None
    to_emails: list[str]
    cc_emails: list[str]
    bcc_emails: list[str]
    created_at: datetime
    ingest_source: str
    folder_role: str | None = None
    folder: str | None = None
    uidvalidity: int | None = None
    imap_uid: int | None = None
    email_task_id: int | None = None
    llm_profile_id: int | None = None
    provider_payload: dict[str, object] | None = None
    reply_headers: dict[str, object] | None = None


def normalize_message_id(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    return normalized or None


def build_message_fingerprint(record: EmailLogIngestRecord) -> str:
    minute = record.created_at.replace(second=0, microsecond=0).isoformat()
    recipients = ",".join(
        sorted(
            [
                *normalize_email_list(record.to_emails),
                *normalize_email_list(record.cc_emails),
                *normalize_email_list(record.bcc_emails),
            ]
        )
    )
    raw = "|".join(
        [
            str(record.identity_id),
            str(record.professor_id),
            record.direction,
            normalize_email_address(record.from_email),
            recipients,
            minute,
            (record.subject or "").strip().lower(),
            sha256(record.content.encode("utf-8")).hexdigest(),
        ]
    )
    return sha256(raw.encode("utf-8")).hexdigest()


async def upsert_email_log(session: AsyncSession, record: EmailLogIngestRecord) -> EmailLog:
    normalized_message_id = normalize_message_id(record.message_id)
    fingerprint = build_message_fingerprint(record)
    existing = await _find_existing_log(session, record, normalized_message_id, fingerprint)
    if existing is None:
        existing = EmailLog(
            email_task_id=record.email_task_id,
            identity_id=record.identity_id,
            llm_profile_id=record.llm_profile_id,
            professor_id=record.professor_id,
            direction=record.direction,
            subject=record.subject,
            content=record.content,
            content_html=record.content_html,
            rfc_message_id=record.message_id,
            normalized_message_id=normalized_message_id,
            provider_payload=record.provider_payload,
            reply_headers=record.reply_headers,
            created_at=record.created_at,
            ingest_source=record.ingest_source,
            folder_role=record.folder_role,
            folder=record.folder,
            uidvalidity=record.uidvalidity,
            imap_uid=record.imap_uid,
            from_email=normalize_email_address(record.from_email),
            to_emails=normalize_email_list(record.to_emails),
            cc_emails=normalize_email_list(record.cc_emails),
            bcc_emails=normalize_email_list(record.bcc_emails),
            message_fingerprint=fingerprint,
            synced_at=utc_now(),
        )
        session.add(existing)
        await session.flush()
        return existing

    _merge_email_log(existing, record, normalized_message_id, fingerprint)
    session.add(existing)
    await session.flush()
    return existing


async def _find_existing_log(
    session: AsyncSession,
    record: EmailLogIngestRecord,
    normalized_message_id: str | None,
    fingerprint: str,
) -> EmailLog | None:
    if normalized_message_id:
        existing = await session.scalar(
            select(EmailLog).where(
                EmailLog.identity_id == record.identity_id,
                EmailLog.professor_id == record.professor_id,
                EmailLog.direction == record.direction,
                EmailLog.normalized_message_id == normalized_message_id,
            )
        )
        if existing is not None:
            return existing
    if record.folder_role and record.folder and record.uidvalidity is not None and record.imap_uid is not None:
        existing = await session.scalar(
            select(EmailLog).where(
                EmailLog.identity_id == record.identity_id,
                EmailLog.professor_id == record.professor_id,
                EmailLog.folder_role == record.folder_role,
                EmailLog.folder == record.folder,
                EmailLog.uidvalidity == record.uidvalidity,
                EmailLog.imap_uid == record.imap_uid,
            )
        )
        if existing is not None:
            return existing
    return await session.scalar(
        select(EmailLog).where(
            EmailLog.identity_id == record.identity_id,
            EmailLog.professor_id == record.professor_id,
            EmailLog.direction == record.direction,
            EmailLog.message_fingerprint == fingerprint,
        )
    )


def _merge_email_log(
    log: EmailLog,
    record: EmailLogIngestRecord,
    normalized_message_id: str | None,
    fingerprint: str,
) -> None:
    if not log.email_task_id and record.email_task_id:
        log.email_task_id = record.email_task_id
    if log.llm_profile_id is None and record.llm_profile_id is not None:
        log.llm_profile_id = record.llm_profile_id
    if not log.subject and record.subject:
        log.subject = record.subject
    if not log.content and record.content:
        log.content = record.content
    if not log.content_html and record.content_html:
        log.content_html = record.content_html
    if not log.rfc_message_id and record.message_id:
        log.rfc_message_id = record.message_id
    if not log.normalized_message_id and normalized_message_id:
        log.normalized_message_id = normalized_message_id
    if not log.provider_payload and record.provider_payload:
        log.provider_payload = record.provider_payload
    if not log.reply_headers and record.reply_headers:
        log.reply_headers = record.reply_headers
    log.ingest_source = log.ingest_source or record.ingest_source
    log.folder_role = log.folder_role or record.folder_role
    log.folder = log.folder or record.folder
    log.uidvalidity = log.uidvalidity if log.uidvalidity is not None else record.uidvalidity
    log.imap_uid = log.imap_uid if log.imap_uid is not None else record.imap_uid
    log.from_email = log.from_email or normalize_email_address(record.from_email)
    log.to_emails = log.to_emails or normalize_email_list(record.to_emails)
    log.cc_emails = log.cc_emails or normalize_email_list(record.cc_emails)
    log.bcc_emails = log.bcc_emails or normalize_email_list(record.bcc_emails)
    log.message_fingerprint = log.message_fingerprint or fingerprint
    log.synced_at = utc_now()
```

- [ ] **步骤 5：运行入库测试**

运行：

```bash
cd backend && uv run python -m unittest test.test_email_log_ingestion
```

预期：`OK`。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/services/email_addresses.py backend/app/services/email_log_ingestion.py backend/test/test_email_log_ingestion.py
git commit -m "feat(backend): add email log ingestion dedupe"
```

## 任务 3：IMAP Sent 文件夹发现和按文件夹拉取

**文件：**
- 修改：`backend/app/services/imap_message_fetcher.py`
- 修改：`backend/app/services/mail_runtime.py`
- 修改：`backend/test/test_mail_runtime.py`

- [ ] **步骤 1：编写失败测试**

在 `backend/test/test_mail_runtime.py` 增加：

```python
from app.services.mail_runtime import (
    discover_sent_folder,
    fetch_incremental_mailbox_messages,
    fetch_professor_history_mailbox_messages,
)


class _SentFolderImapClient(_FakeImapClient):
    def list(self):
        self.commands.append("list")
        return "OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren \\Sent) "/" "Sent"',
        ]


def test_discovers_special_use_sent_folder(self) -> None:
    client = _SentFolderImapClient()

    with patch("app.services.mail_runtime._open_imap_client", return_value=client):
        folder = asyncio.run(discover_sent_folder(_build_identity()))

    self.assertEqual(folder, "Sent")
    self.assertIn("list", client.commands)


def test_incremental_sent_fetch_selects_sent_folder_and_parses_recipients(self) -> None:
    client = _FakeImapClient(search_data=b"1")

    with patch("app.services.mail_runtime._open_imap_client", return_value=client):
        _, messages = asyncio.run(
            fetch_incremental_mailbox_messages(_build_identity(), "Sent", None),
        )

    self.assertIn("select:Sent", client.commands)
    self.assertEqual(messages[0].to_emails, ["sender@example.com"])


def test_sent_history_searches_to_and_cc(self) -> None:
    client = _FakeImapClient(search_data=b"1")

    with patch("app.services.mail_runtime._open_imap_client", return_value=client):
        messages = asyncio.run(
            fetch_professor_history_mailbox_messages(
                _build_identity(),
                "Sent",
                "teacher@example.com",
                folder_role="sent",
            ),
        )

    self.assertIn('(TO "teacher@example.com")', client.search_criteria)
    self.assertIn('(CC "teacher@example.com")', client.search_criteria)
    self.assertEqual(len(messages), 1)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest test.test_mail_runtime
```

预期：FAIL，原因是新函数和 `ImapFetchedMessage.to_emails` 不存在。

- [ ] **步骤 3：扩展 IMAP 消息结构和搜索函数**

在 `backend/app/services/imap_message_fetcher.py`：

```python
@dataclass(slots=True)
class ImapFetchedMessage:
    uid: int
    from_email: str
    subject: str | None
    message_id: str | None
    in_reply_to: str | None
    references: str | None
    sent_at: datetime
    received_at: datetime | None
    headers: dict[str, str]
    body_text: str
    body_html: str | None
    to_emails: list[str] = field(default_factory=list)
    cc_emails: list[str] = field(default_factory=list)
    bcc_emails: list[str] = field(default_factory=list)
    raw_from: str = ""
    raw_to: str = ""
    raw_cc: str = ""
    raw_bcc: str = ""
    has_attachments: bool = False
    attachment_names: list[str] = field(default_factory=list)


def search_uids_to_recipient(client: object, to_email: str) -> list[int]:
    escaped = to_email.replace('"', '\\"')
    status, payload = client.uid("SEARCH", None, f'(TO "{escaped}")')
    if status != "OK" or not payload:
        return []
    raw = payload[0] if payload else b""
    return [int(item) for item in raw.split() if item.isdigit()]


def search_uids_cc_recipient(client: object, cc_email: str) -> list[int]:
    escaped = cc_email.replace('"', '\\"')
    status, payload = client.uid("SEARCH", None, f'(CC "{escaped}")')
    if status != "OK" or not payload:
        return []
    raw = payload[0] if payload else b""
    return [int(item) for item in raw.split() if item.isdigit()]
```

- [ ] **步骤 4：实现文件夹发现和通用拉取**

在 `backend/app/services/mail_runtime.py`：

```python
SENT_FOLDER_CANDIDATES = ("Sent", "Sent Messages", "Sent Mail", "已发送", "已发送邮件", "发件箱")


async def discover_sent_folder(identity: IdentityProfile) -> str | None:
    if not identity.imap_host or not identity.imap_username or not identity.imap_password:
        return None
    return await asyncio.to_thread(_discover_sent_folder_sync, identity)


async def fetch_incremental_mailbox_messages(
    identity: IdentityProfile,
    folder: str,
    last_seen_uid: int | None,
) -> tuple[int | None, list[ImapFetchedMessage]]:
    if not identity.imap_host or not identity.imap_username or not identity.imap_password:
        return last_seen_uid, []
    return await asyncio.to_thread(_fetch_incremental_mailbox_messages_sync, identity, folder, last_seen_uid)


async def fetch_professor_history_mailbox_messages(
    identity: IdentityProfile,
    folder: str,
    professor_email: str,
    *,
    folder_role: str,
) -> list[ImapFetchedMessage]:
    if not identity.imap_host or not identity.imap_username or not identity.imap_password:
        return []
    return await asyncio.to_thread(
        _fetch_professor_history_mailbox_messages_sync,
        identity,
        folder,
        professor_email,
        folder_role,
    )
```

实现同步函数时复用现有 `_fetch_message_by_uid_sync`。新增 `_select_mailbox_or_raise(client, folder)`，并让 `_open_logged_in_imap_client(identity, folder="INBOX")` 选择传入文件夹。旧 `_select_inbox_or_raise` 调用 `_select_mailbox_or_raise(client, "INBOX")`。

在 `_parse_fetched_headers` 中解析收件人：

```python
from app.services.email_addresses import normalize_email_list

raw_to = parsed.get("To", "")
raw_cc = parsed.get("Cc", "")
raw_bcc = parsed.get("Bcc", "")
return ImapFetchedMessage(
    uid=uid,
    from_email=from_email,
    subject=subject,
    message_id=message_id,
    in_reply_to=in_reply_to,
    references=references,
    sent_at=sent_at,
    received_at=received_at,
    headers=headers,
    body_text=body_text or "",
    body_html=body_html,
    to_emails=normalize_email_list([raw_to]),
    cc_emails=normalize_email_list([raw_cc]),
    bcc_emails=normalize_email_list([raw_bcc]),
    raw_from=parsed.get("From", ""),
    raw_to=raw_to,
    raw_cc=raw_cc,
    raw_bcc=raw_bcc,
)
```

- [ ] **步骤 5：保留旧函数兼容包装**

在 `mail_runtime.py` 中保留：

```python
async def fetch_incremental_inbox_messages(
    identity: IdentityProfile,
    last_seen_uid: int | None,
) -> tuple[int | None, list[ImapFetchedMessage]]:
    return await fetch_incremental_mailbox_messages(identity, "INBOX", last_seen_uid)


async def fetch_professor_history_inbox_messages(
    identity: IdentityProfile,
    professor_email: str,
) -> list[ImapFetchedMessage]:
    return await fetch_professor_history_mailbox_messages(
        identity,
        "INBOX",
        professor_email,
        folder_role="inbox",
    )
```

- [ ] **步骤 6：运行 IMAP runtime 测试**

运行：

```bash
cd backend && uv run python -m unittest test.test_mail_runtime
```

预期：`OK`。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/services/imap_message_fetcher.py backend/app/services/mail_runtime.py backend/test/test_mail_runtime.py
git commit -m "feat(backend): discover and fetch sent mailbox messages"
```

## 任务 4：同步状态、增量游标和老师历史补扫

**文件：**
- 修改：`backend/app/services/imap_sync_state.py`
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/test/test_imap_sync_runtime.py`

- [ ] **步骤 1：编写失败测试**

在 `backend/test/test_imap_sync_runtime.py` 增加：

```python
def test_ensure_professor_scan_states_tracks_all_existing_professors_for_inbox_and_sent(self) -> None:
    async def scenario() -> list[tuple[str, str]]:
        async with self.session_factory() as session:
            identity = self._build_identity()
            llm = self._build_llm()
            contacted = Professor(name="Contacted", email="contacted@example.edu")
            untouched = Professor(name="Untouched", email="untouched@example.edu")
            session.add_all([identity, llm, contacted, untouched])
            await session.commit()

        await ensure_professor_scan_states(self.session_factory, identity_id=None, sent_folder="Sent")

        async with self.session_factory() as session:
            rows = list((await session.execute(select(ImapProfessorSyncState))).scalars())
            return sorted((row.professor_email, row.folder_role) for row in rows)

    self.assertEqual(
        self._run_async(scenario()),
        [
            ("contacted@example.edu", "inbox"),
            ("contacted@example.edu", "sent"),
            ("untouched@example.edu", "inbox"),
            ("untouched@example.edu", "sent"),
        ],
    )


def test_sent_incremental_sync_imports_sent_mail_for_matching_professor(self) -> None:
    async def scenario() -> tuple[int, str]:
        identity_id, professor_id = await self._create_identity_and_professor("teacher@example.edu")
        message = self._build_fetched_message(
            message_id="<sent-external@example.com>",
            content="sent body",
        )
        message.from_email = "student@example.com"
        message.to_emails = ["teacher@example.edu"]

        with patch(
            "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages",
            new=AsyncMock(return_value=(42, [message])),
        ):
            result = await sync_identity_incremental_once(
                self.session_factory,
                identity_id,
                folder_role="sent",
                folder="Sent",
            )

        async with self.session_factory() as session:
            log = await session.scalar(select(EmailLog).where(EmailLog.rfc_message_id == "<sent-external@example.com>"))
            state = await session.scalar(
                select(ImapMailboxSyncState).where(
                    ImapMailboxSyncState.identity_id == identity_id,
                    ImapMailboxSyncState.folder_role == "sent",
                )
            )
            return result, log.direction, state.last_seen_uid

    self.assertEqual(self._run_async(scenario()), (1, EmailDirection.SENT.value, 42))
```

添加测试辅助：

```python
async def _create_identity_and_professor(self, email: str) -> tuple[int, int]:
    async with self.session_factory() as session:
        identity = self._build_identity()
        professor = Professor(name=email, email=email)
        session.add_all([identity, professor])
        await session.commit()
        return identity.id, professor.id
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest test.test_imap_sync_runtime
```

预期：FAIL，原因是 `ensure_professor_scan_states` 参数、`folder_role` 同步路径和 Sent 入库不存在。

- [ ] **步骤 3：扩展扫描状态创建**

在 `backend/app/services/imap_sync_state.py`：

```python
async def ensure_professor_scan_states(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int | None = None,
    sent_folder: str | None = None,
) -> int:
    created = 0
    async with session_factory() as session:
        rows = await _load_professor_rows_for_imap_identities(session, identity_id=identity_id)
        for row_identity_id, professor_id, professor_email in rows:
            for folder_role, folder in [("inbox", "INBOX"), ("sent", sent_folder)]:
                if folder_role == "sent" and not folder:
                    continue
                normalized_email = _normalize_email(professor_email)
                if not normalized_email:
                    continue
                existing = await session.scalar(
                    select(ImapProfessorSyncState).where(
                        ImapProfessorSyncState.identity_id == row_identity_id,
                        ImapProfessorSyncState.professor_id == professor_id,
                        ImapProfessorSyncState.professor_email == normalized_email,
                        ImapProfessorSyncState.folder_role == folder_role,
                        ImapProfessorSyncState.folder == folder,
                    )
                )
                if existing is not None:
                    continue
                session.add(
                    ImapProfessorSyncState(
                        identity_id=row_identity_id,
                        professor_id=professor_id,
                        professor_email=normalized_email,
                        folder_role=folder_role,
                        folder=folder or "INBOX",
                    )
                )
                created += 1
        await session.commit()
    return created


async def _load_professor_rows_for_imap_identities(
    session: AsyncSession,
    *,
    identity_id: int | None,
) -> list[tuple[int, int, str | None]]:
    statement = (
        select(IdentityProfile.id, Professor.id, Professor.email)
        .join(Professor, Professor.archived_at.is_(None))
        .where(
            IdentityProfile.imap_host.is_not(None),
            IdentityProfile.imap_username.is_not(None),
            IdentityProfile.imap_password.is_not(None),
            Professor.email.is_not(None),
        )
    )
    if identity_id is not None:
        statement = statement.where(IdentityProfile.id == identity_id)
    return _dedupe_rows((await session.execute(statement)).all())
```

保留 `_load_contacted_professor_rows` 的调用方需要切换到新函数，或删除旧函数。

- [ ] **步骤 4：扩展增量同步**

在 `task_runtime.py` 调整函数签名：

```python
async def sync_identity_incremental_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    *,
    folder_role: str = "inbox",
    folder: str = "INBOX",
) -> int:
    async with session_factory() as session:
        identity = await session.get(IdentityProfile, identity_id)
        if identity is None:
            return 0
        state = await _get_or_create_mailbox_state(
            session,
            identity_id,
            folder_role=folder_role,
            folder=folder,
        )
        last_seen_uid = state.last_seen_uid
        await session.commit()
    try:
        max_seen_uid, messages = await mail_runtime.fetch_incremental_mailbox_messages(
            identity,
            folder,
            last_seen_uid,
        )
    except Exception as exc:
        async with session_factory() as session:
            state = await _get_or_create_mailbox_state(
                session,
                identity_id,
                folder_role=folder_role,
                folder=folder,
            )
            state.last_error = str(exc)
            await session.commit()
        return 0
    detected = await process_imap_fetched_messages(
        session_factory,
        identity_id,
        messages,
        folder_role=folder_role,
        folder=folder,
    )
    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(
            session,
            identity_id,
            folder_role=folder_role,
            folder=folder,
        )
        state.last_seen_uid = max_seen_uid
        state.last_sync_at = utc_now()
        state.last_error = None
        await session.commit()
    return detected
```

`_get_or_create_mailbox_state` 改为按 `identity_id + folder_role + folder` 查询。

- [ ] **步骤 5：统一处理 INBOX 和 Sent 消息**

把 `process_imap_fetched_messages` 改成：

```python
async def process_imap_fetched_messages(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    messages: list[ImapFetchedMessage],
    *,
    folder_role: str = "inbox",
    folder: str = "INBOX",
) -> int:
    if folder_role == "sent":
        return await _process_sent_messages(session_factory, identity_id, messages, folder=folder)
    return await _process_incoming_messages(session_factory, identity_id, messages, folder=folder)
```

实现 `_process_sent_messages`：

```python
async def _process_sent_messages(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    messages: list[ImapFetchedMessage],
    *,
    folder: str,
) -> int:
    detected = 0
    for message in messages:
        async with session_factory() as session:
            professor_ids = await _find_professors_by_emails(
                session,
                [*message.to_emails, *message.cc_emails, *message.bcc_emails],
            )
            for professor_id in professor_ids:
                task = await _find_latest_task_for_professor(session, identity_id, professor_id)
                log = await upsert_email_log(
                    session,
                    EmailLogIngestRecord(
                        identity_id=identity_id,
                        professor_id=professor_id,
                        direction=EmailDirection.SENT.value,
                        subject=message.subject,
                        content=message.body_text,
                        content_html=message.body_html,
                        message_id=message.message_id,
                        from_email=message.from_email,
                        to_emails=message.to_emails,
                        cc_emails=message.cc_emails,
                        bcc_emails=message.bcc_emails,
                        created_at=message.sent_at,
                        ingest_source="imap",
                        folder_role="sent",
                        folder=folder,
                        uidvalidity=None,
                        imap_uid=message.uid,
                        email_task_id=task.id if task else None,
                        llm_profile_id=task.llm_profile_id if task else None,
                        provider_payload={"imap_uid": message.uid, "folder": folder},
                    ),
                )
                if task is not None and task.sent_at is None:
                    task.sent_at = message.sent_at
                    task.status = EmailTaskStatus.SENT.value
                    task.last_rfc_message_id = task.last_rfc_message_id or message.message_id
                    task.updated_at = utc_now()
                detected += 1
            await session.commit()
    return detected
```

导入 `upsert_email_log`、`EmailLogIngestRecord`。

- [ ] **步骤 6：让后台同步两个文件夹**

在 `_sync_identity_imap_once_unlocked`：

```python
sent_folder = await mail_runtime.discover_sent_folder(identity)
await ensure_professor_scan_states(
    session_factory,
    identity_id=identity_id,
    sent_folder=sent_folder,
)
history_detected = await sync_identity_history_once(session_factory, identity_id)
inbox_detected = await sync_identity_incremental_once(
    session_factory,
    identity_id,
    folder_role="inbox",
    folder="INBOX",
)
sent_detected = 0
if sent_folder:
    sent_detected = await sync_identity_incremental_once(
        session_factory,
        identity_id,
        folder_role="sent",
        folder=sent_folder,
    )
return history_detected + inbox_detected + sent_detected
```

先在函数开头加载 `identity`，避免 `discover_sent_folder` 没有身份对象。

- [ ] **步骤 7：历史补扫支持 folder_role**

在 `sync_identity_history_once` 中按状态字段调用：

```python
messages = await mail_runtime.fetch_professor_history_mailbox_messages(
    identity,
    state.folder,
    state.professor_email,
    folder_role=state.folder_role,
)
detected = await process_imap_fetched_messages(
    session_factory,
    identity_id,
    messages,
    folder_role=state.folder_role,
    folder=state.folder,
)
```

- [ ] **步骤 8：运行同步测试**

运行：

```bash
cd backend && uv run python -m unittest test.test_imap_sync_runtime test.test_concurrency_guards
```

预期：`OK`。

- [ ] **步骤 9：Commit**

```bash
git add backend/app/services/imap_sync_state.py backend/app/services/task_runtime.py backend/test/test_imap_sync_runtime.py
git commit -m "feat(backend): sync inbox and sent history for professors"
```

## 任务 5：工作区、老师状态和统计统一读取通信记录

**文件：**
- 修改：`backend/app/api/workspace_support.py`
- 修改：`backend/app/services/contact_status.py`
- 修改：`backend/app/services/dashboard_stats.py`
- 修改：`backend/test/test_workspace_support.py`
- 修改：`backend/test/test_contact_status.py`
- 修改：`backend/test/test_dashboard_stats.py`

- [ ] **步骤 1：编写失败测试**

在 `backend/test/test_workspace_support.py` 增加：

```python
def test_workspace_thread_shows_unbound_imap_messages_without_task(self) -> None:
    async def scenario() -> list[str]:
        async with self.session_factory() as session:
            identity = self._build_identity()
            llm = self._build_llm()
            professor = Professor(name="老师", email="teacher@example.edu")
            session.add_all([identity, llm, professor])
            await session.flush()
            session.add(
                EmailLog(
                    email_task_id=None,
                    identity_id=identity.id,
                    llm_profile_id=None,
                    professor_id=professor.id,
                    direction=EmailDirection.SENT.value,
                    subject="External",
                    content="外部发送",
                    ingest_source="imap",
                    created_at=datetime(2026, 6, 30, tzinfo=UTC),
                )
            )
            await session.commit()
            thread = await build_workspace_thread(
                session,
                professor_id=professor.id,
                identity_id=identity.id,
                llm_profile_id=llm.id,
            )
            return [message.content for message in thread.messages]

    self.assertEqual(self._run_async(scenario()), ["外部发送"])
```

在 `backend/test/test_contact_status.py` 增加：

```python
def test_received_only_log_marks_professor_replied(self) -> None:
    async def scenario() -> str:
        identity_id, professor_id, llm_id = await self._create_identity_professor_llm()
        async with self.session_factory() as session:
            session.add(
                EmailLog(
                    identity_id=identity_id,
                    llm_profile_id=llm_id,
                    professor_id=professor_id,
                    direction=EmailDirection.RECEIVED.value,
                    subject="Re",
                    content="老师主动来信",
                )
            )
            await session.commit()
        async with self.session_factory() as session:
            statuses = await build_contact_status_by_professor(
                session,
                identity_id=identity_id,
                professor_ids=[professor_id],
            )
            return statuses[professor_id].status

    self.assertEqual(self._run_async(scenario()), "replied")
```

在 `backend/test/test_dashboard_stats.py` 增加：

```python
def test_dashboard_counts_imap_sent_and_received_logs_without_tasks(self) -> None:
    identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())

    async def seed_external_logs() -> None:
        async with self.session_factory() as session:
            professor = Professor(name="外部老师", email="external@example.edu")
            session.add(professor)
            await session.flush()
            session.add_all(
                [
                    EmailLog(
                        email_task_id=None,
                        identity_id=identity_id,
                        llm_profile_id=None,
                        professor_id=professor.id,
                        direction=EmailDirection.SENT.value,
                        subject="External",
                        content="外部发送",
                        ingest_source="imap",
                        created_at=datetime.now(UTC) - timedelta(hours=3),
                    ),
                    EmailLog(
                        email_task_id=None,
                        identity_id=identity_id,
                        llm_profile_id=None,
                        professor_id=professor.id,
                        direction=EmailDirection.RECEIVED.value,
                        subject="Re: External",
                        content="外部回复",
                        ingest_source="imap",
                        created_at=datetime.now(UTC) - timedelta(hours=1),
                    ),
                ]
            )
            await session.commit()

    self._run_async(seed_external_logs())

    async def run_query():
        async with self.session_factory() as session:
            return await build_dashboard_overview(
                session,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
            )

    result = self._run_async(run_query())

    self.assertEqual(result.email.summary.sent_count, 4)
    self.assertEqual(result.email.summary.contacted_professor_count, 3)
    self.assertEqual(result.email.summary.replied_count, 2)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest test.test_workspace_support test.test_contact_status test.test_dashboard_stats
```

预期：至少 dashboard 新测试失败，因为当前统计只从 archived_at 为空的初始 professor 列表和任务/日志组合中计算部分口径。

- [ ] **步骤 3：确认工作区查询保持统一记录**

`backend/app/api/workspace_support.py` 当前已经按 `EmailLog.professor_id + identity_id` 读取非草稿日志。确认保留这个行为，只需保证草稿过滤仍限定当前任务：

```python
message_filters = [
    EmailLog.professor_id == professor.id,
    EmailLog.identity_id == identity.id,
]
if current_task is not None:
    message_filters.append(
        or_(
            EmailLog.direction != EmailDirection.DRAFT.value,
            EmailLog.email_task_id == current_task.id,
        )
    )
else:
    message_filters.append(EmailLog.direction != EmailDirection.DRAFT.value)
```

如果测试已经通过，不要为了本任务重构工作区。

- [ ] **步骤 4：调整老师状态**

在 `backend/app/services/contact_status.py`：

```python
def resolve_professor_contact_status(
    tasks: list[EmailTask],
    *,
    sent_count: int = 0,
    has_reply: bool = False,
) -> str:
    if has_reply or any(task.is_replied or task.status == EmailTaskStatus.REPLY_DETECTED.value for task in tasks):
        return "replied"
    if sent_count > 0 or any(task.status == EmailTaskStatus.SENT.value or task.sent_at for task in tasks):
        return "contacted"
    ...
```

确保 `has_reply` 来自所有 `EmailLog.direction == received`，包括无任务日志。现有代码已经按日志方向设置 `last_replied_at_by_professor`，如果新测试失败，修复教授 ID 集合和空任务路径。

- [ ] **步骤 5：调整统计口径**

在 `_build_email_section` 中：

```python
sent_events = [...]
sent_professor_ids = {professor_id for _, professor_id, _ in sent_events}
received_professor_ids: set[int] = set()
...
for log in received_logs:
    if log.professor_id is None:
        continue
    if not _professor_matches_school_filters(log.professor, university=email_university, school=email_school):
        continue
    if not _datetime_in_range(log.created_at, start_at=start_at, end_at=end_at):
        continue
    received_professor_ids.add(log.professor_id)
    received_trend_logs.append(log)

contacted_professor_ids = sent_professor_ids | received_professor_ids
replied_professor_ids = set(received_professor_ids)
```

保留失败发送日志排除逻辑。`replied_fallback_tasks` 继续支持旧数据，但要使用新的 `contacted_professor_ids`。

- [ ] **步骤 6：运行状态和统计测试**

运行：

```bash
cd backend && uv run python -m unittest test.test_workspace_support test.test_contact_status test.test_dashboard_stats
```

预期：`OK`。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/api/workspace_support.py backend/app/services/contact_status.py backend/app/services/dashboard_stats.py backend/test/test_workspace_support.py backend/test/test_contact_status.py backend/test/test_dashboard_stats.py
git commit -m "feat(backend): count unified email communications"
```

## 任务 6：端到端验证、兼容测试和文档校准

**文件：**
- 修改：`docs/superpowers/specs/2026-06-30-unified-email-history-sync-design.md`，仅当实现过程中发现必须修正的规格措辞
- 修改：`backend/test/test_unified_email_log_models.py`
- 修改：`backend/test/test_email_log_ingestion.py`
- 修改：`backend/test/test_mail_runtime.py`
- 修改：`backend/test/test_imap_sync_models.py`
- 修改：`backend/test/test_imap_sync_runtime.py`
- 修改：`backend/test/test_contact_status.py`
- 修改：`backend/test/test_dashboard_stats.py`
- 修改：`backend/test/test_workspace_support.py`
- 修改：`backend/test/test_concurrency_guards.py`

- [ ] **步骤 1：运行后端聚焦测试**

运行：

```bash
cd backend && uv run python -m unittest \
  test.test_unified_email_log_models \
  test.test_email_log_ingestion \
  test.test_mail_runtime \
  test.test_imap_sync_models \
  test.test_imap_sync_runtime \
  test.test_contact_status \
  test.test_dashboard_stats \
  test.test_workspace_support \
  test.test_concurrency_guards
```

预期：`OK`。

- [ ] **步骤 2：运行完整后端测试**

运行：

```bash
cd backend && uv run python -m unittest discover test
```

预期：`OK`。如果出现与本次字段可空、唯一约束或统计口径相关的失败，优先修正测试或实现，不跳过测试。

- [ ] **步骤 3：运行迁移验证**

运行：

```bash
cd backend && uv run alembic upgrade head
```

预期：命令退出码为 0。

- [ ] **步骤 4：运行前端类型检查或相关测试**

如果后端 API schema 没有变更，运行最小前端类型验证：

```bash
cd frontend && npm run build
```

预期：TypeScript 编译通过，Vite build 成功。

如果因为环境或依赖导致无法运行，记录完整错误并在最终汇报中说明。

- [ ] **步骤 5：规格覆盖自检**

逐项核对规格验收标准：

- 用户邮箱客户端发给已有老师的邮件会进入工作区。
- 老师状态和统计发送数更新。
- 老师回复后进入工作区并更新回复状态。
- 系统发送邮件被 Sent 扫到不重复。
- 新增老师后生成 INBOX 和 Sent 历史补扫。
- 无关邮件只推进游标，不下载正文。
- Sent 发现失败不阻塞 INBOX。
- UIDVALIDITY 变化不全邮箱扫描。

如果某项未覆盖，补测试或补实现后重新运行相关测试。

- [ ] **步骤 6：Commit 最终收尾**

```bash
git status --short
git add docs/superpowers/specs/2026-06-30-unified-email-history-sync-design.md \
  backend/test/test_unified_email_log_models.py \
  backend/test/test_email_log_ingestion.py \
  backend/test/test_mail_runtime.py \
  backend/test/test_imap_sync_models.py \
  backend/test/test_imap_sync_runtime.py \
  backend/test/test_contact_status.py \
  backend/test/test_dashboard_stats.py \
  backend/test/test_workspace_support.py \
  backend/test/test_concurrency_guards.py
git commit -m "test: verify unified email history sync"
```

如果没有文件变更，不创建空提交。
