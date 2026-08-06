# 近两自然年邮箱历史同步实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 用“当前自然年 + 上一自然年”的真实邮件 UID 同步策略替换旧的倒序 UID 区间历史扫描，保证几千老师但少量真实发信的场景可以快速同步老师通信记录。

**架构：** 保留现有文件夹级增量同步和 `email_logs` 去重机制。新增近两自然年历史同步路径：Sent 阶段使用 `UID SEARCH SINCE` 枚举真实已发送邮件 UID 并匹配老师；INBOX 阶段只对已联系老师执行 `FROM + SINCE` targeted 搜索。旧 mailbox history completed 门槛不再参与运行入口。

**技术栈：** FastAPI 后端、SQLAlchemy async ORM、SQLite/Postgres 迁移、Python `unittest`、IMAP `UID SEARCH`/`UID FETCH`、现有 `upsert_email_log` 去重。

---

## 规格来源

- 设计文档：`docs/superpowers/specs/2026-07-07-recent-email-history-sync-design.md`
- 旧 IMAP 设计：`docs/superpowers/specs/2026-06-30-unified-email-history-sync-design.md`
- 现有去重：`backend/app/services/email_log_ingestion.py`
- 当前问题：旧 `sync_identity_history_once` 通过 `fetch_history_mailbox_message_headers_before_uid` 倒序扫描 UID 数字区间，在 163 邮箱十亿级 UID 上无法完成。

## 文件结构

- 修改：`backend/app/services/imap_message_fetcher.py`
  - 增加 `UID SEARCH SINCE` 和 `FROM + SINCE` 的低层搜索 helper。

- 修改：`backend/app/services/mail_runtime.py`
  - 增加按自然年窗口枚举真实 UID 并批量拉 header 的运行时函数。
  - 给现有 professor header fetch 增加可选 `since_date`，用于 INBOX targeted 回复补齐。

- 修改：`backend/app/services/imap_sync_state.py`
  - 增加近期历史策略版本常量与 candidate professor state 创建/重置 helper。
  - 移除新流程对 `_mailbox_history_completed_for_targeted_catchup` 的依赖。

- 修改：`backend/app/services/task_runtime.py`
  - 将 `sync_identity_history_once` 改为近两自然年流程入口。
  - 新增 Sent 近期发现、INBOX candidate 构建和 targeted 补齐 orchestration。
  - 保留增量同步和现有 `process_imap_fetched_messages`/`upsert_email_log` 路径。

- 创建：`backend/alembic/versions/20260707_recent_email_history_sync.py`
  - 为 `imap_professor_sync_states` 增加 `history_strategy_version` 字段，用于自然年窗口变化时重置 targeted state。

- 修改：`backend/app/models/imap_sync.py`
  - 为 `ImapProfessorSyncState` 增加 `history_strategy_version` 模型字段。

- 修改：`backend/test/test_imap_message_fetcher.py`
  - 覆盖 `SINCE` 搜索命令生成。

- 修改：`backend/test/test_mail_runtime.py`
  - 覆盖 Sent 近期 header 搜索与 professor INBOX `FROM + SINCE` 搜索。

- 修改：`backend/test/test_imap_sync_models.py`
  - 覆盖新增模型字段和迁移默认值。

- 修改：`backend/test/test_imap_sync_runtime.py`
  - 替换旧倒序区间历史扫描入口测试，新增近两自然年 Sent/INBOX 流程、去重和 legacy pending 状态不阻塞测试。

---

### 任务 1：增加 IMAP `SINCE` 搜索 helper

**文件：**
- 修改：`backend/app/services/imap_message_fetcher.py`
- 测试：`backend/test/test_imap_message_fetcher.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/test/test_imap_message_fetcher.py` 的 import 列表中加入新函数：

```python
from datetime import date

from app.services.imap_message_fetcher import (
    fetch_message_headers_by_uid,
    fetch_text_part_sections_by_uid,
    parse_text_parts_from_message,
    search_uids_from_sender,
    search_uids_from_sender_since,
    search_uids_since,
    search_uids_since_date,
)
```

在 `ImapMessageFetcherTestCase` 中添加：

```python
    def test_search_uids_since_date_uses_imap_since_criterion(self) -> None:
        client = FakeImapClient(search_payload=b"5 7")

        result = search_uids_since_date(client, date(2025, 1, 1))

        self.assertEqual(result, [5, 7])
        serialized = " ".join(str(item) for item in client.commands)
        self.assertIn("SINCE 01-Jan-2025", serialized)
        self.assertNotIn("1:*", serialized)

    def test_search_from_sender_since_combines_sender_and_date(self) -> None:
        client = FakeImapClient(search_payload=b"8 9")

        result = search_uids_from_sender_since(client, "Prof <prof@example.edu>", date(2025, 1, 1))

        self.assertEqual(result, [8, 9])
        serialized = " ".join(str(item) for item in client.commands)
        self.assertIn('FROM "prof@example.edu"', serialized)
        self.assertIn("SINCE 01-Jan-2025", serialized)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest test.test_imap_message_fetcher.ImapMessageFetcherTestCase.test_search_uids_since_date_uses_imap_since_criterion test.test_imap_message_fetcher.ImapMessageFetcherTestCase.test_search_from_sender_since_combines_sender_and_date
```

预期：FAIL，报错包含 `cannot import name 'search_uids_since_date'` 或 `cannot import name 'search_uids_from_sender_since'`。

- [ ] **步骤 3：实现最少搜索 helper**

在 `backend/app/services/imap_message_fetcher.py` 顶部 import 增加：

```python
from datetime import date
from email.utils import parseaddr
```

在 `search_uids_since` 后添加：

```python
def search_uids_since_date(client: object, since_date: date) -> list[int]:
    criterion = f"SINCE {_format_imap_search_date(since_date)}"
    status, payload = client.uid("SEARCH", None, criterion)
    return _parse_uid_search_payload(status, payload)


def search_uids_from_sender_since(client: object, from_email: str, since_date: date) -> list[int]:
    normalized = parseaddr(from_email)[1] or from_email
    escaped = _escape_imap_search_value(normalized)
    criterion = f'(FROM "{escaped}" SINCE {_format_imap_search_date(since_date)})'
    status, payload = client.uid("SEARCH", None, criterion)
    return _parse_uid_search_payload(status, payload)


def _format_imap_search_date(value: date) -> str:
    return value.strftime("%d-%b-%Y")
```

保留现有 `search_uids_from_sender`、`search_uids_to_recipient` 等函数不变。

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_imap_message_fetcher
```

预期：PASS，所有 `ImapMessageFetcherTestCase` 测试通过。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/imap_message_fetcher.py backend/test/test_imap_message_fetcher.py
git commit -m "feat(imap): add since date uid search helpers"
```

---

### 任务 2：增加近期历史 IMAP runtime fetch

**文件：**
- 修改：`backend/app/services/mail_runtime.py`
- 测试：`backend/test/test_mail_runtime.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/test/test_mail_runtime.py` 的 import 中加入新函数：

```python
from datetime import UTC, date, datetime

from app.services.mail_runtime import (
    MailRuntimeError,
    discover_sent_folder,
    fetch_inbox_messages_from_sender,
    fetch_incremental_inbox_messages,
    fetch_incremental_mailbox_messages,
    fetch_incremental_mailbox_messages_with_uidvalidity,
    fetch_recent_mailbox_message_headers_since,
    fetch_history_mailbox_message_headers_before_uid,
    fetch_professor_history_inbox_messages,
    fetch_professor_history_mailbox_message_headers,
    fetch_professor_history_mailbox_message_headers_with_command_count,
    fetch_professor_history_mailbox_messages,
    fetch_professor_history_mailbox_messages_by_uid,
    format_imap_login_error,
    _test_imap_connection_sync,
    parse_received_email,
    send_email_to_recipient,
)
```

在 `MailRuntimeTestCase` 中添加测试：

```python
    def test_recent_mailbox_headers_searches_real_uids_since_date(self) -> None:
        client = _FakeImapClient(
            search_data_by_criterion={"SINCE 01-Jan-2025": b"7 9"},
            headers_by_uid={
                7: (
                    b"From: sender@example.com\r\n"
                    b"To: teacher@example.edu\r\n"
                    b"Subject: first\r\n"
                    b"Message-ID: <recent-7@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:00:00 +0800\r\n\r\n"
                ),
                9: (
                    b"From: sender@example.com\r\n"
                    b"To: other@example.edu\r\n"
                    b"Subject: second\r\n"
                    b"Message-ID: <recent-9@example.com>\r\n"
                    b"Date: Fri, 09 May 2026 20:00:00 +0800\r\n\r\n"
                ),
            },
        )
        client.response = lambda code: ("UIDVALIDITY", [b"777"])

        with patch("app.services.mail_runtime._open_imap_client", return_value=client):
            result = asyncio.run(
                fetch_recent_mailbox_message_headers_since(
                    _build_identity(),
                    "Sent",
                    date(2025, 1, 1),
                    min_uid=None,
                    max_fetch_batches=None,
                ),
            )

        self.assertEqual([message.uid for message in result.messages], [7, 9])
        self.assertEqual([message.uidvalidity for message in result.messages], [777, 777])
        self.assertIn("select:Sent", client.commands)
        self.assertIn("SINCE 01-Jan-2025", client.search_criteria)
        self.assertFalse(any(":" in criterion and "SINCE" not in criterion for criterion in client.search_criteria))

    def test_professor_history_headers_accept_since_date_for_inbox_search(self) -> None:
        client = _FakeImapClient(
            search_data_by_criterion={
                '(FROM "teacher@example.edu" SINCE 01-Jan-2025)': b"4",
            },
            headers_by_uid={
                4: (
                    b"From: teacher@example.edu\r\n"
                    b"To: student@example.com\r\n"
                    b"Subject: reply\r\n"
                    b"Message-ID: <reply-4@example.edu>\r\n"
                    b"Date: Fri, 08 May 2026 20:00:00 +0800\r\n\r\n"
                ),
            },
        )
        client.response = lambda code: ("UIDVALIDITY", [b"888"])

        with patch("app.services.mail_runtime._open_imap_client", return_value=client):
            result = asyncio.run(
                fetch_professor_history_mailbox_message_headers_with_command_count(
                    _build_identity(),
                    "INBOX",
                    "teacher@example.edu",
                    folder_role="inbox",
                    min_uid=None,
                    max_fetch_batches=None,
                    since_date=date(2025, 1, 1),
                ),
            )

        self.assertEqual([message.uid for message in result.messages], [4])
        self.assertEqual(result.messages[0].uidvalidity, 888)
        self.assertIn('(FROM "teacher@example.edu" SINCE 01-Jan-2025)', client.search_criteria)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest test.test_mail_runtime.MailRuntimeTestCase.test_recent_mailbox_headers_searches_real_uids_since_date test.test_mail_runtime.MailRuntimeTestCase.test_professor_history_headers_accept_since_date_for_inbox_search
```

预期：FAIL，先报 `cannot import name 'fetch_recent_mailbox_message_headers_since'`，实现该函数后第二个测试会因 `unexpected keyword argument 'since_date'` 失败。

- [ ] **步骤 3：实现 runtime 函数**

在 `backend/app/services/mail_runtime.py` import 中加入：

```python
from datetime import date
```

从 `app.services.imap_message_fetcher` import 新 helper：

```python
from app.services.imap_message_fetcher import (
    ImapFetchedMessage,
    ImapFetchCommandError,
    ImapSearchResult,
    fetch_message_headers_payload_by_uid,
    fetch_message_headers_payloads_by_uid_batch,
    fetch_message_headers_payloads_by_uid_range,
    fetch_text_part_sections_by_uid,
    parse_text_parts_from_message,
    search_uids_bcc_recipient,
    search_uids_cc_recipient,
    search_uids_combined_sent_recipient,
    search_uids_from_sender,
    search_uids_from_sender_since,
    search_uids_since,
    search_uids_since_date,
    search_uids_to_recipient,
)
```

在 async wrappers 附近添加：

```python
async def fetch_recent_mailbox_message_headers_since(
    identity: IdentityProfile,
    folder: str,
    since_date: date,
    *,
    min_uid: int | None,
    max_fetch_batches: int | None,
) -> ImapHistoryHeaderFetchResult:
    return await asyncio.to_thread(
        _fetch_recent_mailbox_message_headers_since_sync,
        identity,
        folder,
        since_date,
        min_uid,
        max_fetch_batches,
    )
```

在 sync helpers 附近添加：

```python
def _fetch_recent_mailbox_message_headers_since_sync(
    identity: IdentityProfile,
    folder: str,
    since_date: date,
    min_uid: int | None,
    max_fetch_batches: int | None,
) -> ImapHistoryHeaderFetchResult:
    client: IMAP4 | IMAP4_SSL | None = None
    messages: list[ImapFetchedMessage] = []
    command_count = 0
    exhausted = False
    try:
        client = _open_logged_in_imap_client(identity, folder=folder)
        uidvalidity = _get_selected_mailbox_uidvalidity(client)
        acquire_history_imap_command_slot_sync(identity, "SEARCH")
        uids = search_uids_since_date(client, since_date)
        command_count += 1
        if min_uid is not None:
            uids = [uid for uid in uids if uid > min_uid]
        batches = _chunked(uids, max(1, get_settings().imap_fetch_batch_size))
        fetch_batches = batches
        if max_fetch_batches is not None and max_fetch_batches < len(batches):
            fetch_batches = batches[: max(0, max_fetch_batches)]
            exhausted = bool(batches)
        for batch in fetch_batches:
            acquire_history_imap_command_slot_sync(identity, "FETCH")
            command_count += 1
            fetched_items = fetch_message_headers_payloads_by_uid_batch(client, batch)
            payloads_by_uid = {uid: payload for uid, payload in fetched_items}
            for uid in batch:
                payload = payloads_by_uid.get(uid)
                if not payload:
                    continue
                raw_headers = _extract_message_bytes_from_fetch_payload(payload)
                if not raw_headers:
                    continue
                received_at = _extract_received_at_from_fetch_payload(payload)
                message = _parse_fetched_headers(uid, raw_headers, "", None, received_at)
                if message is not None:
                    message.uidvalidity = uidvalidity
                    messages.append(message)
    except MailRuntimeError:
        raise
    except OSError as exc:
        raise MailRuntimeError(f"IMAP 近期历史头部同步失败: {exc}") from exc
    finally:
        _logout_imap_client(client)
    return ImapHistoryHeaderFetchResult(
        messages=messages,
        command_count=command_count,
        exhausted=exhausted,
    )
```

更新 `fetch_professor_history_mailbox_message_headers_with_command_count` 和 sync 版本签名，增加仅关键字参数：

```python
async def fetch_professor_history_mailbox_message_headers_with_command_count(
    identity: IdentityProfile,
    folder: str,
    professor_email: str,
    *,
    folder_role: str,
    min_uid: int | None = None,
    max_fetch_batches: int | None = None,
    since_date: date | None = None,
) -> ImapHistoryHeaderFetchResult:
```

同步版本 `_fetch_professor_history_mailbox_message_headers_with_command_count_sync` 也添加 `since_date: date | None` 参数，并传入 `_search_professor_history_uids`：

```python
search_result = _search_professor_history_uids(
    identity,
    client,
    professor_email,
    folder_role=folder_role,
    since_date=since_date,
)
```

更新 `_search_professor_history_uids`：

```python
def _search_professor_history_uids(
    identity: IdentityProfile,
    client: IMAP4 | IMAP4_SSL,
    professor_email: str,
    *,
    folder_role: str,
    since_date: date | None = None,
) -> _ImapHistorySearchResult:
    if folder_role == "inbox":
        acquire_history_imap_command_slot_sync(identity, "SEARCH")
        if since_date is not None:
            return _ImapHistorySearchResult(
                uids=search_uids_from_sender_since(client, professor_email, since_date),
                command_count=1,
            )
        return _ImapHistorySearchResult(
            uids=search_uids_from_sender(client, professor_email),
            command_count=1,
        )
    ...
```

Keep the existing sent branch unchanged when `since_date` is `None`; first version does not need per-professor Sent targeted recent search because Sent recent discovery scans the Sent mailbox by real UID.

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_mail_runtime.MailRuntimeTestCase.test_recent_mailbox_headers_searches_real_uids_since_date test.test_mail_runtime.MailRuntimeTestCase.test_professor_history_headers_accept_since_date_for_inbox_search
```

预期：PASS。

再运行：

```bash
cd backend && uv run python -m unittest test.test_mail_runtime
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/mail_runtime.py backend/test/test_mail_runtime.py
git commit -m "feat(imap): fetch recent history headers by real uid search"
```

---

### 任务 3：为老师历史状态增加策略版本

**文件：**
- 修改：`backend/app/models/imap_sync.py`
- 创建：`backend/alembic/versions/20260707_recent_email_history_sync.py`
- 修改：`backend/test/test_imap_sync_models.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/test/test_imap_sync_models.py` 的 `ImapSyncModelsTestCase` 中添加：

```python
    def test_professor_sync_state_records_history_strategy_version(self) -> None:
        async def scenario() -> str:
            async with self.session_factory() as session:
                identity = IdentityProfile(
                    name="Identity",
                    profile_name="Identity",
                    sender_name="Student",
                    email_address="student@example.com",
                    smtp_host="smtp.example.com",
                    smtp_port=465,
                    smtp_username="student@example.com",
                    smtp_password="secret",
                    imap_host="imap.example.com",
                    imap_port=993,
                    imap_username="student@example.com",
                    imap_password="secret",
                )
                professor = Professor(name="Prof", email="prof@example.edu")
                session.add_all([identity, professor])
                await session.flush()
                state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=professor.id,
                    professor_email="prof@example.edu",
                    folder_role="inbox",
                    folder="INBOX",
                    history_strategy_version="recent-v1-2025",
                )
                session.add(state)
                await session.commit()

                saved = await session.scalar(select(ImapProfessorSyncState))
                return saved.history_strategy_version

        self.assertEqual(self._run_async(scenario()), "recent-v1-2025")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest test.test_imap_sync_models.ImapSyncModelsTestCase.test_professor_sync_state_records_history_strategy_version
```

预期：FAIL，报 `TypeError: 'history_strategy_version' is an invalid keyword argument` 或 `AttributeError`。

- [ ] **步骤 3：添加模型字段**

在 `backend/app/models/imap_sync.py` 的 `ImapProfessorSyncState` 中添加：

```python
    history_strategy_version: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'legacy'"),
    )
```

建议放在 `historical_scan_status` 后，`last_scanned_uid` 前。

- [ ] **步骤 4：添加 Alembic 迁移**

创建 `backend/alembic/versions/20260707_recent_email_history_sync.py`：

```python
"""add recent email history strategy version

Revision ID: 20260707_recent_email_history_sync
Revises: 20260703_imap_folder_history_scan
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260707_recent_email_history_sync"
down_revision = "20260703_imap_folder_history_scan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "imap_professor_sync_states",
        sa.Column(
            "history_strategy_version",
            sa.String(length=32),
            nullable=False,
            server_default="legacy",
        ),
    )


def downgrade() -> None:
    op.drop_column("imap_professor_sync_states", "history_strategy_version")
```

用以下命令确认当前 head 仍为 `20260703_imap_folder_history_scan`：

```bash
cd backend && uv run alembic heads
```

如果命令输出已经变化，先停下更新本迁移的 `down_revision`，避免创建错误分支。

- [ ] **步骤 5：运行测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_imap_sync_models
```

预期：PASS。

再运行：

```bash
cd backend && uv run alembic upgrade head
```

预期：迁移成功；如果本地已有数据库，命令不报错。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/models/imap_sync.py backend/alembic/versions/20260707_recent_email_history_sync.py backend/test/test_imap_sync_models.py
git commit -m "feat(imap): track professor history strategy version"
```

---

### 任务 4：实现近期历史状态与候选老师 helper

**文件：**
- 修改：`backend/app/services/imap_sync_state.py`
- 测试：`backend/test/test_imap_sync_runtime.py`

- [ ] **步骤 1：编写失败的测试**

更新 `backend/test/test_imap_sync_runtime.py` import：

```python
from app.services.imap_sync_state import (
    clear_identity_sent_folder_discovery_cache,
    ensure_professor_scan_states,
    ensure_professor_scan_states_if_needed,
    ensure_recent_history_professor_scan_states,
)
```

如果该文件当前使用多行分别 import，就按现有风格加入 `ensure_recent_history_professor_scan_states`。

在 `ImapSyncRuntimeTestCase` 中添加：

```python
    def test_recent_history_candidate_states_reset_when_strategy_changes(self) -> None:
        async def scenario() -> tuple[int, str, int | None, str]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Known", email="Known@Example.edu")
                session.add_all([identity, professor])
                await session.flush()
                state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=professor.id,
                    professor_email="known@example.edu",
                    folder_role="inbox",
                    folder="INBOX",
                    historical_scan_status=ImapProfessorHistoricalScanStatus.COMPLETED.value,
                    last_scanned_uid=900,
                    history_strategy_version="recent-v1-2024",
                )
                session.add(state)
                await session.commit()
                identity_id = identity.id
                professor_id = professor.id

            created = await ensure_recent_history_professor_scan_states(
                self.session_factory,
                identity_id=identity_id,
                candidates={(professor_id, "known@example.edu")},
                strategy_version="recent-v1-2025",
                folder="INBOX",
            )

            async with self.session_factory() as session:
                saved = await session.scalar(select(ImapProfessorSyncState))
                return (
                    created,
                    saved.historical_scan_status,
                    saved.last_scanned_uid,
                    saved.history_strategy_version,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (0, ImapProfessorHistoricalScanStatus.PENDING.value, None, "recent-v1-2025"),
        )

    def test_recent_history_candidate_states_create_only_candidates(self) -> None:
        async def scenario() -> list[tuple[str, str]]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                chosen = Professor(name="Chosen", email="chosen@example.edu")
                skipped = Professor(name="Skipped", email="skipped@example.edu")
                session.add_all([identity, chosen, skipped])
                await session.commit()
                identity_id = identity.id
                chosen_id = chosen.id

            await ensure_recent_history_professor_scan_states(
                self.session_factory,
                identity_id=identity_id,
                candidates={(chosen_id, "Chosen@Example.edu")},
                strategy_version="recent-v1-2025",
                folder="INBOX",
            )

            async with self.session_factory() as session:
                rows = list(
                    (
                        await session.execute(
                            select(ImapProfessorSyncState).order_by(ImapProfessorSyncState.professor_email),
                        )
                    ).scalars(),
                )
                return [(row.professor_email, row.history_strategy_version) for row in rows]

        self.assertEqual(self._run_async(scenario()), [("chosen@example.edu", "recent-v1-2025")])
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest test.test_imap_sync_runtime.ImapSyncRuntimeTestCase.test_recent_history_candidate_states_reset_when_strategy_changes test.test_imap_sync_runtime.ImapSyncRuntimeTestCase.test_recent_history_candidate_states_create_only_candidates
```

预期：FAIL，报 `cannot import name 'ensure_recent_history_professor_scan_states'`。

- [ ] **步骤 3：实现 helper**

在 `backend/app/services/imap_sync_state.py` 添加常量：

```python
RECENT_HISTORY_STRATEGY_PREFIX = "recent-v1"
```

添加函数：

```python
RecentHistoryCandidate = tuple[int, str]


async def ensure_recent_history_professor_scan_states(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    candidates: set[RecentHistoryCandidate],
    strategy_version: str,
    folder: str = "INBOX",
) -> int:
    normalized_candidates = {
        (professor_id, _normalize_email(email))
        for professor_id, email in candidates
        if professor_id and _normalize_email(email)
    }
    if not normalized_candidates:
        return 0

    created = 0
    async with session_factory() as session:
        desired_keys = [
            (identity_id, professor_id, professor_email, "inbox", folder)
            for professor_id, professor_email in sorted(normalized_candidates)
        ]
        existing_keys = await _load_existing_scan_state_keys(session, desired_keys)
        existing_rows = list(
            (
                await session.execute(
                    select(ImapProfessorSyncState).where(
                        ImapProfessorSyncState.identity_id == identity_id,
                        ImapProfessorSyncState.folder_role == "inbox",
                        ImapProfessorSyncState.folder == folder,
                        ImapProfessorSyncState.professor_email.in_(
                            [email for _, email in normalized_candidates],
                        ),
                    ),
                )
            ).scalars(),
        )
        for row in existing_rows:
            if row.history_strategy_version != strategy_version:
                row.history_strategy_version = strategy_version
                row.historical_scan_status = ImapProfessorHistoricalScanStatus.PENDING.value
                row.last_scanned_uid = None
                row.historical_scan_started_at = None
                row.historical_scan_completed_at = None
                row.last_error = None

        for key in desired_keys:
            if key in existing_keys:
                continue
            row_identity_id, professor_id, professor_email, folder_role, row_folder = key
            session.add(
                ImapProfessorSyncState(
                    identity_id=row_identity_id,
                    professor_id=professor_id,
                    professor_email=professor_email,
                    folder_role=folder_role,
                    folder=row_folder,
                    historical_scan_status=ImapProfessorHistoricalScanStatus.PENDING.value,
                    history_strategy_version=strategy_version,
                ),
            )
            created += 1
        await session.commit()
    return created
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_imap_sync_runtime.ImapSyncRuntimeTestCase.test_recent_history_candidate_states_reset_when_strategy_changes test.test_imap_sync_runtime.ImapSyncRuntimeTestCase.test_recent_history_candidate_states_create_only_candidates
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/imap_sync_state.py backend/test/test_imap_sync_runtime.py
git commit -m "feat(imap): ensure recent history professor states"
```

---

### 任务 5：实现自然年窗口和 Sent 近期发现 orchestration

**文件：**
- 修改：`backend/app/services/task_runtime.py`
- 测试：`backend/test/test_imap_sync_runtime.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/test/test_imap_sync_runtime.py` 中添加：

```python
    def test_recent_history_window_uses_current_and_previous_calendar_year(self) -> None:
        from app.services.task_runtime import build_recent_history_window

        self.assertEqual(
            build_recent_history_window(datetime(2026, 7, 7, 12, 0, tzinfo=UTC)).start_date.isoformat(),
            "2025-01-01",
        )
        self.assertEqual(
            build_recent_history_window(datetime(2027, 1, 1, 0, 0, tzinfo=UTC)).strategy_version,
            "recent-v1-2026",
        )

    def test_history_sync_discovers_sent_recent_messages_by_real_uid_search(self) -> None:
        async def scenario() -> tuple[int, list[dict[str, object]], list[tuple[int, str, str]]]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor_a = Professor(name="A", email="a@example.edu")
                professor_b = Professor(name="B", email="b@example.edu")
                professor_c = Professor(name="C", email="c@example.edu")
                session.add_all([identity, professor_a, professor_b, professor_c])
                await session.commit()
                identity_id = identity.id

            header = self._build_fetched_message(
                uid=51,
                uidvalidity=777,
                message_id="<multi-teacher@example.com>",
                from_email="student@example.com",
                to_emails=["A <a@example.edu>", "b@example.edu", "stranger@example.edu"],
                subject="Hello",
                content="",
            )
            body = self._build_fetched_message(
                uid=51,
                uidvalidity=777,
                message_id="<multi-teacher@example.com>",
                from_email="student@example.com",
                to_emails=["A <a@example.edu>", "b@example.edu", "stranger@example.edu"],
                subject="Hello",
                content="sent body",
            )
            header_calls: list[dict[str, object]] = []

            async def fake_recent_headers(_identity, _folder, since_date, *, min_uid, max_fetch_batches):
                header_calls.append(
                    {
                        "folder": _folder,
                        "since_date": since_date.isoformat(),
                        "min_uid": min_uid,
                        "max_fetch_batches": max_fetch_batches,
                    },
                )
                return ImapHistoryHeaderFetchResult(messages=[header], command_count=2, exhausted=False)

            with (
                patch("app.services.task_runtime.get_cached_or_discover_sent_folder", new=AsyncMock(return_value="Sent")),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_recent_mailbox_message_headers_since",
                    new=AsyncMock(side_effect=fake_recent_headers),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(return_value=[body]),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_history_mailbox_message_headers_before_uid",
                    new=AsyncMock(side_effect=AssertionError("legacy uid range scan must not run")),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(return_value=ImapHistoryHeaderFetchResult(messages=[], command_count=1)),
                ),
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                logs = list(
                    (
                        await session.execute(
                            select(EmailLog).order_by(EmailLog.professor_id),
                        )
                    ).scalars(),
                )
                return detected, header_calls, [
                    (log.professor_id, log.direction, log.content) for log in logs
                ]

        detected, header_calls, logs = self._run_async(scenario())
        self.assertEqual(detected, 2)
        self.assertEqual(header_calls[0]["folder"], "Sent")
        self.assertEqual(header_calls[0]["since_date"], "2025-01-01")
        self.assertEqual(logs, [(1, "sent", "sent body"), (2, "sent", "sent body")])
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest test.test_imap_sync_runtime.ImapSyncRuntimeTestCase.test_recent_history_window_uses_current_and_previous_calendar_year test.test_imap_sync_runtime.ImapSyncRuntimeTestCase.test_history_sync_discovers_sent_recent_messages_by_real_uid_search
```

预期：FAIL，先报 `cannot import name 'build_recent_history_window'`；实现窗口 helper 后，第二个测试会因旧 `fetch_history_mailbox_message_headers_before_uid` 被调用而失败。

- [ ] **步骤 3：实现窗口 helper**

在 `backend/app/services/task_runtime.py` import 增加：

```python
from dataclasses import dataclass
from datetime import date
```

添加：

```python
RECENT_HISTORY_STRATEGY_NAME = "recent-v1"


@dataclass(frozen=True, slots=True)
class RecentHistoryWindow:
    start_date: date
    strategy_version: str


def build_recent_history_window(now: datetime | None = None) -> RecentHistoryWindow:
    current = now or utc_now()
    start_year = current.year - 1
    return RecentHistoryWindow(
        start_date=date(start_year, 1, 1),
        strategy_version=f"{RECENT_HISTORY_STRATEGY_NAME}-{start_year}",
    )
```

- [ ] **步骤 4：实现 Sent 近期发现 helper**

在 `task_runtime.py` 添加 dataclass：

```python
@dataclass(slots=True)
class _RecentSentDiscoveryResult:
    detected: int
    professor_candidates: set[tuple[int, str]]
    command_count: int
```

添加 helper：

```python
async def _sync_recent_sent_history_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityProfile,
    *,
    sent_folder: str,
    window: RecentHistoryWindow,
    command_budget: int,
) -> _RecentSentDiscoveryResult:
    if command_budget <= 0:
        return _RecentSentDiscoveryResult(detected=0, professor_candidates=set(), command_count=0)
    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(
            session,
            identity.id,
            folder_role="sent",
            folder=sent_folder,
        )
        if state.history_strategy_version != window.strategy_version:
            state.history_strategy_version = window.strategy_version
            state.history_high_water_uid = None
            state.history_next_before_uid = None
            state.history_scan_status = "sent_recent_discovery_pending"
            state.history_scanned_count = 0
            state.history_matched_count = 0
            await session.commit()
        min_uid = state.history_high_water_uid

    header_result = await mail_runtime.fetch_recent_mailbox_message_headers_since(
        identity,
        sent_folder,
        window.start_date,
        min_uid=min_uid,
        max_fetch_batches=max(1, min(command_budget, get_settings().imap_history_batch_size)),
    )

    professor_ids_by_email = await _load_active_professor_ids_by_email(session_factory)
    matched_headers: list[_MailboxHistoryHeaderMatch] = []
    professor_candidates: set[tuple[int, str]] = set()
    for message in header_result.messages:
        recipient_emails = normalize_email_list([*message.to_emails, *message.cc_emails, *message.bcc_emails])
        professor_ids = tuple(
            dict.fromkeys(
                professor_id
                for email in recipient_emails
                for professor_id in professor_ids_by_email.get(email, [])
            ),
        )
        if professor_ids:
            matched_headers.append(_MailboxHistoryHeaderMatch(message=message, professor_ids=professor_ids))
            for email in recipient_emails:
                for professor_id in professor_ids_by_email.get(email, []):
                    professor_candidates.add((professor_id, email))

    body_result = await _fetch_recent_sent_message_bodies(
        session_factory,
        identity,
        identity_id=identity.id,
        folder=sent_folder,
        matched_headers=matched_headers,
        remaining_command_budget=max(0, command_budget - header_result.command_count),
    )
    detected = await process_imap_fetched_messages(
        session_factory,
        identity.id,
        body_result.messages,
        folder_role="sent",
        folder=sent_folder,
    )
    max_seen_uid = max([min_uid or 0, *[message.uid for message in header_result.messages]], default=min_uid or 0)
    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(session, identity.id, folder_role="sent", folder=sent_folder)
        state.history_high_water_uid = max_seen_uid or None
        state.history_scanned_count = (state.history_scanned_count or 0) + len(header_result.messages)
        state.history_matched_count = (state.history_matched_count or 0) + len(matched_headers)
        state.history_scan_status = "inbox_recent_replies_pending" if not header_result.exhausted else "sent_recent_discovery_running"
        state.history_last_error = None
        await session.commit()
    return _RecentSentDiscoveryResult(
        detected=detected,
        professor_candidates=professor_candidates,
        command_count=header_result.command_count + body_result.command_count,
    )
```

Add helper for bodies:

```python
async def _fetch_recent_sent_message_bodies(
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityProfile,
    *,
    identity_id: int,
    folder: str,
    matched_headers: list[_MailboxHistoryHeaderMatch],
    remaining_command_budget: int,
) -> _MailboxHistoryBodyFetchResult:
    if not matched_headers:
        return _MailboxHistoryBodyFetchResult(
            messages=[],
            command_count=0,
            matched_header_count=0,
            covered_all_headers=True,
        )
    missing_uids: list[int] = []
    allowed_uid_count = _history_body_fetch_uid_limit(
        remaining_command_budget,
        get_settings().imap_fetch_batch_size,
    )
    for match in matched_headers:
        if allowed_uid_count <= 0:
            break
        if await _recent_sent_header_already_ingested(
            session_factory,
            identity_id=identity_id,
            folder=folder,
            message=match.message,
            professor_ids=match.professor_ids,
        ):
            continue
        if len(missing_uids) >= allowed_uid_count:
            break
        missing_uids.append(match.message.uid)
    if not missing_uids:
        return _MailboxHistoryBodyFetchResult(
            messages=[],
            command_count=0,
            matched_header_count=len(matched_headers),
            covered_all_headers=True,
        )
    messages = await mail_runtime.fetch_professor_history_mailbox_messages_by_uid(
        identity,
        folder,
        missing_uids,
    )
    return _MailboxHistoryBodyFetchResult(
        messages=messages,
        command_count=_history_body_fetch_command_count(len(missing_uids), get_settings().imap_fetch_batch_size),
        matched_header_count=len(matched_headers),
        covered_all_headers=True,
    )
```

在同一区域新增直接查询 helper，避免构造临时 ORM state：

```python
async def _recent_sent_header_already_ingested(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    folder: str,
    message: ImapFetchedMessage,
    professor_ids: tuple[int, ...],
) -> bool:
    if not professor_ids:
        return True
    expected_professor_ids = set(professor_ids)
    normalized_message_id = (message.message_id or "").strip().lower()
    async with session_factory() as session:
        if normalized_message_id:
            rows = (
                await session.execute(
                    select(EmailLog.professor_id).where(
                        EmailLog.identity_id == identity_id,
                        EmailLog.professor_id.in_(expected_professor_ids),
                        EmailLog.direction == EmailDirection.SENT.value,
                        or_(
                            EmailLog.normalized_message_id == normalized_message_id,
                            func.lower(EmailLog.rfc_message_id) == normalized_message_id,
                        ),
                    ),
                )
            ).all()
            if {professor_id for (professor_id,) in rows} >= expected_professor_ids:
                return True
        if message.uidvalidity is None:
            return False
        rows = (
            await session.execute(
                select(EmailLog.professor_id).where(
                    EmailLog.identity_id == identity_id,
                    EmailLog.professor_id.in_(expected_professor_ids),
                    EmailLog.folder_role == "sent",
                    EmailLog.folder == folder,
                    EmailLog.uidvalidity == message.uidvalidity,
                    EmailLog.imap_uid == message.uid,
                ),
            )
        ).all()
    return {professor_id for (professor_id,) in rows} >= expected_professor_ids
```

- [ ] **步骤 5：接入 `sync_identity_history_once`**

Replace the old claim-next-mailbox-history block in `sync_identity_history_once` with:

```python
    window = build_recent_history_window()
    sent_discovery = _RecentSentDiscoveryResult(detected=0, professor_candidates=set(), command_count=0)
    if sent_folder and command_budget > 0:
        sent_discovery = await _sync_recent_sent_history_once(
            session_factory,
            identity,
            sent_folder=sent_folder,
            window=window,
            command_budget=command_budget,
        )
        command_budget = max(0, command_budget - sent_discovery.command_count)

    await ensure_recent_history_professor_scan_states(
        session_factory,
        identity_id=identity_id,
        candidates=await _load_recent_history_inbox_candidates(
            session_factory,
            identity_id=identity_id,
            sent_candidates=sent_discovery.professor_candidates,
        ),
        strategy_version=window.strategy_version,
        folder="INBOX",
    )

    inbox_detected = await _sync_identity_targeted_history_once(
        session_factory,
        identity_id,
        command_budget=command_budget,
        mailbox_folders=[("inbox", "INBOX"), ("sent", sent_folder)] if sent_folder else [("inbox", "INBOX")],
        since_date=window.start_date,
        strategy_version=window.strategy_version,
    )
    await log_imap_history_progress(session_factory, identity_id, folders=[("inbox", "INBOX")])
    return sent_discovery.detected + inbox_detected
```

Import `ensure_recent_history_professor_scan_states` from `app.services.imap_sync_state`.

Do not call `claim_next_mailbox_history_scans` or `fetch_history_mailbox_message_headers_before_uid` from this new entry path.

- [ ] **步骤 6：运行测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_imap_sync_runtime.ImapSyncRuntimeTestCase.test_recent_history_window_uses_current_and_previous_calendar_year test.test_imap_sync_runtime.ImapSyncRuntimeTestCase.test_history_sync_discovers_sent_recent_messages_by_real_uid_search
```

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/services/task_runtime.py backend/test/test_imap_sync_runtime.py
git commit -m "feat(imap): discover recent sent history by real uids"
```

---

### 任务 6：实现 INBOX 候选老师集合与近期 targeted 补齐

**文件：**
- 修改：`backend/app/services/task_runtime.py`
- 测试：`backend/test/test_imap_sync_runtime.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/test/test_imap_sync_runtime.py` 添加：

```python
    def test_recent_inbox_history_searches_only_contacted_candidates_since_window(self) -> None:
        async def scenario() -> tuple[int, list[dict[str, object]], list[tuple[str, str]]]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                contacted = Professor(name="Contacted", email="contacted@example.edu")
                unrelated = Professor(name="Unrelated", email="unrelated@example.edu")
                llm = self._build_llm()
                session.add_all([identity, contacted, unrelated, llm])
                await session.flush()
                session.add(
                    EmailLog(
                        identity_id=identity.id,
                        llm_profile_id=llm.id,
                        professor_id=contacted.id,
                        direction=EmailDirection.SENT.value,
                        subject="Existing contact",
                        content="hello",
                    ),
                )
                await session.commit()
                identity_id = identity.id

            inbox_header = self._build_fetched_message(
                uid=61,
                uidvalidity=888,
                message_id="<reply-contacted@example.edu>",
                from_email="contacted@example.edu",
                to_emails=["student@example.com"],
                subject="Re: Existing contact",
                content="",
            )
            inbox_body = self._build_fetched_message(
                uid=61,
                uidvalidity=888,
                message_id="<reply-contacted@example.edu>",
                from_email="contacted@example.edu",
                to_emails=["student@example.com"],
                subject="Re: Existing contact",
                content="reply body",
            )
            targeted_calls: list[dict[str, object]] = []

            async def fake_targeted(_identity, _folder, professor_email, *, folder_role, min_uid, max_fetch_batches, since_date):
                targeted_calls.append(
                    {
                        "professor_email": professor_email,
                        "folder_role": folder_role,
                        "since_date": since_date.isoformat(),
                    },
                )
                return ImapHistoryHeaderFetchResult(
                    messages=[inbox_header] if professor_email == "contacted@example.edu" else [],
                    command_count=1,
                    exhausted=False,
                )

            with (
                patch("app.services.task_runtime.get_cached_or_discover_sent_folder", new=AsyncMock(return_value="Sent")),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_recent_mailbox_message_headers_since",
                    new=AsyncMock(return_value=ImapHistoryHeaderFetchResult(messages=[], command_count=1, exhausted=False)),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(side_effect=fake_targeted),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(return_value=[inbox_body]),
                ),
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                logs = list(
                    (
                        await session.execute(
                            select(EmailLog).where(EmailLog.direction == EmailDirection.RECEIVED.value),
                        )
                    ).scalars(),
                )
                return detected, targeted_calls, [(log.from_email, log.content) for log in logs]

        detected, targeted_calls, logs = self._run_async(scenario())
        self.assertEqual(detected, 1)
        self.assertEqual(targeted_calls, [{"professor_email": "contacted@example.edu", "folder_role": "inbox", "since_date": "2025-01-01"}])
        self.assertEqual(logs, [("contacted@example.edu", "reply body")])
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest test.test_imap_sync_runtime.ImapSyncRuntimeTestCase.test_recent_inbox_history_searches_only_contacted_candidates_since_window
```

预期：FAIL，通常会因为 `_sync_identity_targeted_history_once` 不接受 `since_date`/`strategy_version`，或不会创建 candidate state。

- [ ] **步骤 3：实现候选集合 helper**

在 `task_runtime.py` 添加：

```python
async def _load_recent_history_inbox_candidates(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    sent_candidates: set[tuple[int, str]],
) -> set[tuple[int, str]]:
    candidates = set(sent_candidates)
    async with session_factory() as session:
        log_rows = (
            await session.execute(
                select(Professor.id, Professor.email)
                .join(EmailLog, EmailLog.professor_id == Professor.id)
                .where(
                    EmailLog.identity_id == identity_id,
                    Professor.archived_at.is_(None),
                    Professor.email.is_not(None),
                )
            )
        ).all()
        for professor_id, email in log_rows:
            normalized = normalize_email_address(email)
            if normalized:
                candidates.add((professor_id, normalized))

        task_rows = (
            await session.execute(
                select(Professor.id, Professor.email)
                .join(EmailTask, EmailTask.professor_id == Professor.id)
                .where(
                    EmailTask.identity_id == identity_id,
                    Professor.archived_at.is_(None),
                    Professor.email.is_not(None),
                    or_(
                        EmailTask.status.in_(
                            [
                                EmailTaskStatus.SENT.value,
                                EmailTaskStatus.REPLY_DETECTED.value,
                            ],
                        ),
                        EmailTask.last_rfc_message_id.is_not(None),
                    ),
                )
            )
        ).all()
        for professor_id, email in task_rows:
            normalized = normalize_email_address(email)
            if normalized:
                candidates.add((professor_id, normalized))
    return candidates
```

This deliberately avoids all draft/pending tasks so a workspace with thousands of unsent professors does not turn into thousands of INBOX searches.

- [ ] **步骤 4：更新 targeted history 函数**

Update `_sync_identity_targeted_history_once` signature:

```python
async def _sync_identity_targeted_history_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    *,
    command_budget: int | None = None,
    mailbox_folders: list[tuple[str, str]] | None = None,
    since_date: date | None = None,
    strategy_version: str | None = None,
) -> int:
```

When claiming states, filter by strategy version if provided. If `claim_next_professor_scans` cannot filter, add a new helper in `imap_sync_state.py` or post-filter claimed states. Prefer adding optional `strategy_version` to `claim_next_professor_scans`:

```python
states = await claim_next_professor_scans(
    session_factory,
    identity_id,
    limit=claim_limit,
    strategy_version=strategy_version,
)
```

Pass `since_date` into `fetch_professor_history_mailbox_message_headers_with_command_count`:

```python
header_result = await mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count(
    identity,
    state.folder,
    state.professor_email,
    folder_role=state.folder_role,
    min_uid=state.last_scanned_uid,
    max_fetch_batches=header_fetch_budget,
    since_date=since_date if state.folder_role == "inbox" else None,
)
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_imap_sync_runtime.ImapSyncRuntimeTestCase.test_recent_inbox_history_searches_only_contacted_candidates_since_window
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/services/task_runtime.py backend/app/services/imap_sync_state.py backend/test/test_imap_sync_runtime.py
git commit -m "feat(imap): sync recent inbox replies for contacted professors"
```

---

### 任务 7：替换旧入口测试并防止倒序 UID 扫描回归

**文件：**
- 修改：`backend/test/test_imap_sync_runtime.py`
- 修改：`backend/app/services/task_runtime.py`

- [ ] **步骤 1：编写失败的回归测试**

在 `backend/test/test_imap_sync_runtime.py` 添加：

```python
    def test_legacy_pending_mailbox_history_does_not_block_recent_history_sync(self) -> None:
        async def scenario() -> tuple[int, int, int]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Known", email="known@example.edu")
                session.add_all([identity, professor])
                await session.flush()
                session.add(
                    ImapMailboxSyncState(
                        identity_id=identity.id,
                        folder_role="inbox",
                        folder="INBOX",
                        history_scan_status=ImapMailboxHistoricalScanStatus.PENDING.value,
                        history_high_water_uid=1743736759,
                        history_next_before_uid=1743648160,
                        history_scanned_count=88600,
                        history_matched_count=0,
                    ),
                )
                await session.commit()
                identity_id = identity.id

            sent_header = self._build_fetched_message(
                uid=1743736938,
                uidvalidity=3,
                message_id="<recent-sent@example.com>",
                from_email="student@example.com",
                to_emails=["known@example.edu"],
                content="",
            )
            sent_body = self._build_fetched_message(
                uid=1743736938,
                uidvalidity=3,
                message_id="<recent-sent@example.com>",
                from_email="student@example.com",
                to_emails=["known@example.edu"],
                content="body",
            )

            with (
                patch("app.services.task_runtime.get_cached_or_discover_sent_folder", new=AsyncMock(return_value="Sent")),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_recent_mailbox_message_headers_since",
                    new=AsyncMock(return_value=ImapHistoryHeaderFetchResult(messages=[sent_header], command_count=2, exhausted=False)),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(return_value=[sent_body]),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_history_mailbox_message_headers_before_uid",
                    new=AsyncMock(side_effect=AssertionError("legacy uid range scan must not run")),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(return_value=ImapHistoryHeaderFetchResult(messages=[], command_count=1)),
                ),
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                log_count = await session.scalar(select(func.count(EmailLog.id)))
                professor_state_count = await session.scalar(select(func.count(ImapProfessorSyncState.id)))
                assert log_count is not None
                assert professor_state_count is not None
                return detected, log_count, professor_state_count

        self.assertEqual(self._run_async(scenario()), (1, 1, 1))
```

- [ ] **步骤 2：运行测试验证失败或确认现状**

运行：

```bash
cd backend && uv run python -m unittest test.test_imap_sync_runtime.ImapSyncRuntimeTestCase.test_legacy_pending_mailbox_history_does_not_block_recent_history_sync
```

预期：在旧代码或未完全替换入口时 FAIL，并触发 `legacy uid range scan must not run`。在任务 5/6 已完全接入后可能已经 PASS；如果已 PASS，保留测试作为回归保护。

- [ ] **步骤 3：删除或更新旧入口断言**

在 `backend/test/test_imap_sync_runtime.py` 中处理旧的 `sync_identity_history_once` 测试：

- 删除或重写任何断言 `fetch_history_mailbox_message_headers_before_uid` 是主历史入口的测试。
- 保留低层 `mail_runtime.fetch_history_mailbox_message_headers_before_uid` 的测试，因为该函数可作为 legacy helper 存在。
- 对旧 `history_scan_status == completed` 才 targeted 的测试，改为断言新流程不依赖 `completed`。

具体搜索：

```bash
cd /Users/junie/Programs/AutoEmailSender
rtk rg -n "fetch_history_mailbox_message_headers_before_uid|history_scan_status|_mailbox_history_scans_completed" backend/test/test_imap_sync_runtime.py
```

每个被更新测试都要表达新口径：

```python
patch(
    "app.services.task_runtime.mail_runtime.fetch_history_mailbox_message_headers_before_uid",
    new=AsyncMock(side_effect=AssertionError("legacy uid range scan must not run")),
)
```

- [ ] **步骤 4：运行 focused runtime tests**

运行：

```bash
cd backend && uv run python -m unittest test.test_imap_sync_runtime
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/task_runtime.py backend/test/test_imap_sync_runtime.py
git commit -m "test(imap): guard recent history against uid range scan"
```

---

### 任务 8：验证去重和全量后端回归

**文件：**
- 修改：`backend/test/test_email_log_ingestion.py`

- [ ] **步骤 1：确认现有去重测试覆盖**

运行：

```bash
cd backend && uv run python -m unittest test.test_email_log_ingestion
```

预期：PASS。若失败，先修复本次改动引起的问题，不改变去重语义。

- [ ] **步骤 2：补充多老师同 Message-ID 去重测试**

在 `backend/test/test_email_log_ingestion.py` 添加：

```python
    def test_allows_same_message_id_for_different_professors(self) -> None:
        async def scenario() -> int:
            async with self.session_factory() as session:
                base = dict(
                    identity_id=1,
                    direction=EmailDirection.SENT.value,
                    subject="Group mail",
                    content="Body",
                    content_html=None,
                    message_id="<group@example.com>",
                    from_email="student@example.com",
                    to_emails=["a@example.edu", "b@example.edu"],
                    cc_emails=None,
                    bcc_emails=None,
                    created_at=datetime(2026, 7, 7, 12, 0, tzinfo=UTC),
                    ingest_source="imap",
                    folder_role="sent",
                    folder="Sent",
                    uidvalidity=3,
                    imap_uid=51,
                    email_task_id=None,
                    llm_profile_id=None,
                    provider_payload=None,
                    reply_headers=None,
                )
                await upsert_email_log(session, EmailLogIngestRecord(professor_id=10, **base))
                await upsert_email_log(session, EmailLogIngestRecord(professor_id=11, **base))
                await session.commit()
                count = await session.scalar(select(func.count()).select_from(EmailLog))
                assert count is not None
                return count

        self.assertEqual(self._run_async(scenario()), 2)
```

- [ ] **步骤 3：运行去重测试**

运行：

```bash
cd backend && uv run python -m unittest test.test_email_log_ingestion
```

预期：PASS。

- [ ] **步骤 4：运行 IMAP 相关测试**

运行：

```bash
cd backend && uv run python -m unittest test.test_imap_message_fetcher test.test_mail_runtime test.test_imap_sync_models test.test_imap_sync_runtime
```

预期：PASS。

- [ ] **步骤 5：运行后端完整测试**

运行：

```bash
cd backend && uv run python -m unittest discover test
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add backend/test/test_email_log_ingestion.py
git commit -m "test(email): cover multi-professor message dedupe"
```

---

### 任务 9：最终人工诊断验证

**文件：**
- 不修改文件，执行只读验证。

- [ ] **步骤 1：确认没有业务代码外的意外改动**

运行：

```bash
git status --short
```

预期：工作区干净，或只包含当前任务明确产生的文件。

- [ ] **步骤 2：确认旧倒序扫描不再从入口调用**

运行：

```bash
rtk rg -n "fetch_history_mailbox_message_headers_before_uid\\(" backend/app/services/task_runtime.py
```

预期：`sync_identity_history_once` 不调用该函数。该函数可以仍在 legacy helper 或测试里存在。

- [ ] **步骤 3：确认近期同步入口使用真实 UID 搜索**

运行：

```bash
rtk rg -n "fetch_recent_mailbox_message_headers_since|build_recent_history_window|ensure_recent_history_professor_scan_states" backend/app/services
```

预期：能看到 `task_runtime.py` 的新入口调用和 `mail_runtime.py` 的 runtime 函数。

- [ ] **步骤 4：记录验证结果**

在最终回复中报告：

```text
已验证：
- cd backend && uv run python -m unittest test.test_imap_message_fetcher test.test_mail_runtime test.test_imap_sync_models test.test_imap_sync_runtime
- cd backend && uv run python -m unittest discover test
```

如果任一命令失败，报告失败测试名和错误摘要，不要声称完成。
