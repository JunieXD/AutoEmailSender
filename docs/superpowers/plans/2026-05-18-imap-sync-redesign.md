# IMAP 回复同步重构实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 IMAP 回复检测从固定 72 小时整封邮件重复扫描，重构为支持 163/QQ 邮箱的有状态、低流量、可恢复同步。

**架构：** 新增身份级 mailbox 游标和导师级历史扫描状态。日常同步按身份 UID 增量拉取轻量头部和文本正文 part；历史补扫按已进入沟通链路的导师逐个 `FROM` 全历史扫描；后台和手动刷新共享身份级并发保护。

**技术栈：** FastAPI、SQLAlchemy async、Alembic、SQLite、Python `imaplib`、`unittest`、`uv`。

---

## 文件结构

- 创建：`backend/app/models/imap_sync.py`，定义 `ImapMailboxSyncState`、`ImapProfessorSyncState`、历史扫描状态枚举。
- 修改：`backend/app/models/__init__.py`，导出新模型。
- 创建：`backend/alembic/versions/*_add_imap_sync_states.py`，只建表和索引，不执行 IMAP 网络请求。
- 创建：`backend/app/services/imap_sync_state.py`，生成导师历史扫描状态、claim pending 状态、推进/失败状态。
- 创建：`backend/app/services/imap_message_fetcher.py`，封装 `UID SEARCH`、头部 fetch、`BODYSTRUCTURE`、文本 part 拉取，禁止默认拉附件。
- 修改：`backend/app/services/mail_runtime.py`，新增轻量 IMAP fetch 入口和 163/QQ 授权码错误提示。
- 修改：`backend/app/services/task_runtime.py`，替换后台 poller、手动刷新、去重合并和任务状态推进。
- 修改：`backend/app/api/workspaces.py`，工作区刷新只同步当前导师。
- 测试：新增 `backend/test/test_imap_sync_models.py`、`backend/test/test_imap_message_fetcher.py`、`backend/test/test_imap_sync_runtime.py`。
- 测试：修改 `backend/test/test_concurrency_guards.py`、`backend/test/test_runtime_manager.py`、`backend/test/test_api_endpoints.py`、`backend/test/test_mail_runtime.py`。

---

### 任务 1：新增 IMAP 同步状态模型

**文件：**
- 创建：`backend/app/models/imap_sync.py`
- 修改：`backend/app/models/__init__.py`
- 测试：`backend/test/test_imap_sync_models.py`

- [ ] **步骤 1：编写失败的模型测试**

创建 `backend/test/test_imap_sync_models.py`：

```python
from __future__ import annotations

import asyncio
import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Base,
    ImapMailboxSyncState,
    ImapProfessorHistoricalScanStatus,
    ImapProfessorSyncState,
)


class ImapSyncModelsTestCase(unittest.TestCase):
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

    def test_mailbox_state_defaults_to_inbox(self) -> None:
        async def scenario() -> str:
            async with self.session_factory() as session:
                session.add(ImapMailboxSyncState(identity_id=1))
                await session.commit()
                saved = await session.scalar(select(ImapMailboxSyncState))
                return saved.folder
        self.assertEqual(self._run_async(scenario()), "INBOX")

    def test_professor_state_defaults_to_pending(self) -> None:
        async def scenario() -> str:
            async with self.session_factory() as session:
                session.add(ImapProfessorSyncState(identity_id=1, professor_id=2, professor_email="prof@example.edu"))
                await session.commit()
                saved = await session.scalar(select(ImapProfessorSyncState))
                return saved.historical_scan_status
        self.assertEqual(self._run_async(scenario()), ImapProfessorHistoricalScanStatus.PENDING.value)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest test.test_imap_sync_models`

预期：FAIL，`ImapMailboxSyncState` 无法导入。

- [ ] **步骤 3：实现模型**

创建 `backend/app/models/imap_sync.py`，包含：

```python
class ImapProfessorHistoricalScanStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
```

`ImapMailboxSyncState` 字段：`id`、`identity_id`、`folder` 默认 `INBOX`、`uidvalidity`、`last_seen_uid`、`last_sync_at`、`last_error`、`created_at`、`updated_at`；唯一约束 `identity_id + folder`。

`ImapProfessorSyncState` 字段：`id`、`identity_id`、`professor_id`、`professor_email`、`folder` 默认 `INBOX`、`historical_scan_status` 默认 `pending`、`last_scanned_uid`、`historical_scan_started_at`、`historical_scan_completed_at`、`last_error`、`created_at`、`updated_at`；唯一约束 `identity_id + professor_id + professor_email + folder`。

修改 `backend/app/models/__init__.py` 导入并加入 `__all__`。

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend && uv run python -m unittest test.test_imap_sync_models`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/models/imap_sync.py backend/app/models/__init__.py backend/test/test_imap_sync_models.py
git commit -m "feat(backend): add imap sync state models"
```

---

### 任务 2：新增数据库迁移

**文件：**
- 创建：`backend/alembic/versions/*_add_imap_sync_states.py`
- 测试：`backend/test/test_imap_sync_models.py`

- [ ] **步骤 1：补充元数据测试**

在 `backend/test/test_imap_sync_models.py` 增加：

```python
    def test_sync_state_tables_exist_in_metadata(self) -> None:
        self.assertIn("imap_mailbox_sync_states", Base.metadata.tables)
        self.assertIn("imap_professor_sync_states", Base.metadata.tables)
        self.assertIn("last_seen_uid", Base.metadata.tables["imap_mailbox_sync_states"].columns)
        self.assertIn("historical_scan_status", Base.metadata.tables["imap_professor_sync_states"].columns)
```

- [ ] **步骤 2：创建迁移**

运行：`cd backend && uv run alembic revision -m "add imap sync states"`

编辑生成文件：`upgrade()` 创建 `imap_mailbox_sync_states` 和 `imap_professor_sync_states`，字段与任务 1 模型一致；创建 `identity_id`、`professor_id`、`professor_email` 索引；`downgrade()` 反向删除索引和表。

- [ ] **步骤 3：运行迁移验证**

运行：`cd backend && uv run alembic upgrade head`

预期：PASS。

- [ ] **步骤 4：运行模型测试**

运行：`cd backend && uv run python -m unittest test.test_imap_sync_models`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/alembic/versions/*_add_imap_sync_states.py backend/test/test_imap_sync_models.py
git commit -m "feat(backend): add imap sync state migration"
```

---

### 任务 3：实现轻量 IMAP 拉取器

**文件：**
- 创建：`backend/app/services/imap_message_fetcher.py`
- 测试：`backend/test/test_imap_message_fetcher.py`

- [ ] **步骤 1：编写失败测试：文本解析忽略附件内容**

创建 `backend/test/test_imap_message_fetcher.py`，构造包含 `text/plain`、`text/html` 和 PDF 附件的 `EmailMessage`，调用 `parse_text_parts_from_message(message)`，断言：

```python
self.assertEqual(parsed.body_text, "plain body\n")
self.assertEqual(parsed.body_html, "<p>html body</p>\n")
self.assertTrue(parsed.has_attachments)
self.assertEqual(parsed.attachment_names, ["cv.pdf"])
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest test.test_imap_message_fetcher`

预期：FAIL，模块不存在。

- [ ] **步骤 3：实现解析模型和函数**

在 `backend/app/services/imap_message_fetcher.py` 创建 `ParsedTextParts`、`ImapFetchedMessage`，实现 `parse_text_parts_from_message(message)`：遍历 MIME part，跳过 `attachment` 或有 filename 的 part，只读取 `text/plain` 与 `text/html`。

- [ ] **步骤 4：编写失败测试：头部拉取不使用 RFC822**

用 Fake IMAP client 记录 `uid("FETCH", ...)` 命令，调用 `fetch_message_headers_by_uid(client, 1)`，断言命令包含 `HEADER.FIELDS` 且不包含 `RFC822`。

- [ ] **步骤 5：实现头部/搜索函数**

实现：

```python
def fetch_message_headers_by_uid(client: object, uid: int) -> bytes:
    status, payload = client.uid(
        "FETCH",
        str(uid),
        "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM TO CC SUBJECT DATE IN-REPLY-TO REFERENCES)] INTERNALDATE)",
    )
    if status != "OK" or not payload:
        return b""
    for item in payload:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return b""


def search_uids_since(client: object, last_seen_uid: int | None) -> list[int]:
    start_uid = 1 if last_seen_uid is None else last_seen_uid + 1
    status, payload = client.uid("SEARCH", None, f"UID {start_uid}:*")
    if status != "OK" or not payload:
        return []
    raw = payload[0] if payload else b""
    return [int(item) for item in raw.split() if item.isdigit()]


def search_uids_from_sender(client: object, from_email: str) -> list[int]:
    escaped = from_email.replace('"', '\\"')
    status, payload = client.uid("SEARCH", None, f'(FROM "{escaped}")')
    if status != "OK" or not payload:
        return []
    raw = payload[0] if payload else b""
    return [int(item) for item in raw.split() if item.isdigit()]
```

`search_uids_since` 使用 `UID {last_seen_uid + 1}:*`；`search_uids_from_sender` 使用 `(FROM "邮箱")`。

- [ ] **步骤 6：运行测试验证通过**

运行：`cd backend && uv run python -m unittest test.test_imap_message_fetcher`

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/services/imap_message_fetcher.py backend/test/test_imap_message_fetcher.py
git commit -m "feat(backend): add lightweight imap message fetcher"
```

---

### 任务 4：生成导师历史扫描队列

**文件：**
- 创建：`backend/app/services/imap_sync_state.py`
- 测试：`backend/test/test_imap_sync_runtime.py`

- [ ] **步骤 1：编写失败测试：只跟踪有沟通链路导师**

创建 `backend/test/test_imap_sync_runtime.py`。构造一个完整 IMAP 身份、一个 LLM、两个导师；只给导师 A 创建 `EmailTask`。调用 `ensure_professor_scan_states(session_factory)` 后断言只生成导师 A 的 `ImapProfessorSyncState`。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest test.test_imap_sync_runtime`

预期：FAIL，服务不存在。

- [ ] **步骤 3：实现 `ensure_professor_scan_states`**

创建 `backend/app/services/imap_sync_state.py`：查询完整配置 IMAP 的 `IdentityProfile`，从 `EmailTask` 和 `EmailLog` 关联 `Professor.email` 找出导师，按 `identity_id + professor_id + professor_email + INBOX` 去重创建 pending 状态。

- [ ] **步骤 4：补充 email_logs 场景测试**

增加测试：导师没有任务但有 `EmailLog`，也要生成 pending 状态。

- [ ] **步骤 5：运行测试验证通过**

运行：`cd backend && uv run python -m unittest test.test_imap_sync_runtime`

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/services/imap_sync_state.py backend/test/test_imap_sync_runtime.py
git commit -m "feat(backend): queue imap professor history scans"
```

---

### 任务 5：实现去重合并与状态推进

**文件：**
- 修改：`backend/app/services/task_runtime.py`
- 测试：`backend/test/test_imap_sync_runtime.py`

- [ ] **步骤 1：编写失败测试：已有非空记录不覆盖**

构造旧 `EmailLog(direction="received", rfc_message_id="<reply@example.edu>", content="old content")`，再用 `process_imap_fetched_messages` 传入同 `Message-ID`、`content="new content"` 的消息，断言最终仍为 `old content`。

- [ ] **步骤 2：编写失败测试：空字段补齐**

构造旧记录 `content=None` 或空字符串，新扫描有正文，断言被补齐。

- [ ] **步骤 3：编写失败测试：取消/回收站任务也改为已回复**

分别构造 `EmailTask.status="canceled"` 和 `EmailTask.deleted_at` 非空的场景，扫描到真实回复后断言：

```python
self.assertEqual(task.status, EmailTaskStatus.REPLY_DETECTED.value)
self.assertTrue(task.is_replied)
```

- [ ] **步骤 4：实现处理入口**

在 `task_runtime.py` 添加：

```python
async def process_imap_fetched_messages(session_factory, identity_id: int, messages: list[ImapFetchedMessage]) -> int:
    received_messages = [
        ReceivedEmail(
            from_email=message.from_email,
            subject=message.subject,
            content=message.body_text,
            content_html=message.body_html,
            message_id=message.message_id,
            in_reply_to=message.in_reply_to,
            references=message.references,
            sent_at=message.sent_at,
            received_at=message.received_at,
            headers=message.headers,
        )
        for message in messages
    ]
    return await _process_incoming_reply_messages(session_factory, identity_id, received_messages)
```

修改 `_process_incoming_reply_messages`：命中同 `Message-ID` 旧记录时不覆盖非空字段，只补齐空字段；发现新回复时统一把关联任务推进到 `reply_detected`，包括取消和回收站任务。

- [ ] **步骤 5：运行测试**

运行：`cd backend && uv run python -m unittest test.test_imap_sync_runtime`

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/services/task_runtime.py backend/test/test_imap_sync_runtime.py
git commit -m "feat(backend): merge imap replies without overwriting logs"
```

---

### 任务 6：身份级并发保护

**文件：**
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/test/test_concurrency_guards.py`

- [ ] **步骤 1：编写失败测试：同一身份 single-flight**

在 `test_concurrency_guards.py` 新增测试：并发两次调用 `sync_identity_imap_once(session_factory, identity_id)`，mock `_sync_identity_imap_once_unlocked` 延迟返回 `1`，断言 mock 只 await 一次，结果总和为 `1`。

- [ ] **步骤 2：实现进程内身份级锁**

在 `task_runtime.py` 添加 `_IMAP_IDENTITY_LOCKS: dict[int, asyncio.Lock]` 与 `_get_imap_identity_lock(identity_id)`；实现：

```python
async def sync_identity_imap_once(session_factory, identity_id: int) -> int:
    lock = await _get_imap_identity_lock(identity_id)
    if lock.locked():
        return 0
    async with lock:
        return await _sync_identity_imap_once_unlocked(session_factory, identity_id)
```

- [ ] **步骤 3：运行测试**

运行：`cd backend && uv run python -m unittest test.test_concurrency_guards`

预期：PASS。

- [ ] **步骤 4：Commit**

```bash
git add backend/app/services/task_runtime.py backend/test/test_concurrency_guards.py
git commit -m "feat(backend): guard imap sync per identity"
```

---

### 任务 7：接入后台渐进历史扫描和 UID 增量同步

**文件：**
- 修改：`backend/app/services/imap_sync_state.py`
- 修改：`backend/app/services/mail_runtime.py`
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/test/test_runtime_manager.py`
- 测试：`backend/test/test_imap_sync_runtime.py`

- [ ] **步骤 1：编写失败测试：每轮只 claim 一个导师**

构造两个 pending `ImapProfessorSyncState`，调用 `claim_next_professor_scan(session_factory, identity_id)`，断言只有一条状态变为 `running`。

- [ ] **步骤 2：实现 claim/complete/fail**

在 `imap_sync_state.py` 实现 `claim_next_professor_scan`、`mark_professor_scan_completed`、`mark_professor_scan_failed`，状态切换分别写入 started/completed/error 字段。

- [ ] **步骤 3：编写失败测试：poller 走 `sync_identity_imap_once`**

mock `sync_identity_imap_once`，调用 `poll_for_replies_once`，断言每个完整 IMAP 身份调用一次，且不再直接调用 `poll_identity_replies`。

- [ ] **步骤 4：修改 poller**

把 `poll_for_replies_once` 中对 `poll_identity_replies` 的调用替换为 `sync_identity_imap_once`。

- [ ] **步骤 5：实现真实同步骨架**

在 `task_runtime.py` 实现：

```python
async def _sync_identity_imap_once_unlocked(session_factory, identity_id: int) -> int:
    await ensure_professor_scan_states(session_factory)
    history_detected = await sync_identity_history_once(session_factory, identity_id)
    incremental_detected = await sync_identity_incremental_once(session_factory, identity_id)
    return history_detected + incremental_detected
```

`sync_identity_history_once` 每轮 claim 一个导师，调用 `mail_runtime.fetch_professor_history_inbox_messages(identity, professor_email)`，处理后 complete/fail。`sync_identity_incremental_once` 读取/创建 `ImapMailboxSyncState`，调用 `mail_runtime.fetch_incremental_inbox_messages(identity, last_seen_uid)`，处理后推进 `last_seen_uid`。

- [ ] **步骤 6：在 `mail_runtime.py` 接入轻量 IMAP fetch**

新增：

```python
async def fetch_incremental_inbox_messages(
    identity: IdentityProfile,
    last_seen_uid: int | None,
) -> tuple[int | None, list[ImapFetchedMessage]]:
    return await asyncio.to_thread(
        _fetch_incremental_inbox_messages_sync,
        identity,
        last_seen_uid,
    )


async def fetch_professor_history_inbox_messages(
    identity: IdentityProfile,
    professor_email: str,
) -> list[ImapFetchedMessage]:
    return await asyncio.to_thread(
        _fetch_professor_history_inbox_messages_sync,
        identity,
        professor_email,
    )
```

内部打开 IMAP、登录、`SELECT INBOX`，用 `UID SEARCH` + 头部/正文 part fetch，禁止使用 `FETCH (RFC822 INTERNALDATE)`。

- [ ] **步骤 7：运行重点测试**

运行：

```bash
cd backend && uv run python -m unittest test.test_imap_message_fetcher
cd backend && uv run python -m unittest test.test_imap_sync_runtime
cd backend && uv run python -m unittest test.test_runtime_manager
```

预期：PASS。

- [ ] **步骤 8：Commit**

```bash
git add backend/app/services/imap_sync_state.py backend/app/services/mail_runtime.py backend/app/services/task_runtime.py backend/test/test_imap_sync_runtime.py backend/test/test_runtime_manager.py
git commit -m "feat(backend): sync imap replies with uid cursors"
```

---

### 任务 8：调整工作区手动刷新

**文件：**
- 修改：`backend/app/api/workspaces.py`
- 修改：`backend/app/services/task_runtime.py`
- 测试：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写失败测试：只同步当前导师**

在 `test_api_endpoints.py` 新增测试：请求 `POST /api/workspaces/{professor_id}/refresh-replies`，mock `app.api.workspaces.sync_workspace_professor_replies`，断言只调用该函数一次，不调用旧 `repair_identity_replies`。

- [ ] **步骤 2：实现当前导师刷新入口**

在 `task_runtime.py` 添加：

```python
async def sync_workspace_professor_replies(session_factory, identity_id: int, professor_id: int) -> int:
    lock = await _get_imap_identity_lock(identity_id)
    if lock.locked():
        return 0
    async with lock:
        # 读取 identity/professor；无邮箱则返回 0
        # 调用 fetch_professor_history_inbox_messages(identity, professor.email)
        # 调用 process_imap_fetched_messages
```

- [ ] **步骤 3：修改路由**

在 `backend/app/api/workspaces.py` 将 `repair_identity_replies` 替换为 `sync_workspace_professor_replies(get_session_factory(), identity_id, professor_id)`。

- [ ] **步骤 4：运行 API 测试**

运行：`cd backend && uv run python -m unittest test.test_api_endpoints`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/api/workspaces.py backend/app/services/task_runtime.py backend/test/test_api_endpoints.py
git commit -m "feat(backend): refresh imap replies per workspace professor"
```

---

### 任务 9：163/QQ 兼容错误提示

**文件：**
- 修改：`backend/app/services/mail_runtime.py`
- 修改：`backend/test/test_mail_runtime.py`
- 可选修改：`frontend/src/components/molecules/ProfileForm.tsx`

- [ ] **步骤 1：编写失败测试：授权码提示**

在 `test_mail_runtime.py` 增加测试：`identity.imap_host="imap.qq.com"` 或 `imap.163.com` 时，`format_imap_login_error(identity, "AUTHENTICATIONFAILED")` 返回内容包含“授权码”和“IMAP/SMTP”。

- [ ] **步骤 2：实现错误格式化**

在 `mail_runtime.py` 添加 `format_imap_login_error(identity, detail)`；对 `imap.qq.com`、`imap.163.com`、`imap.126.com`、`imap.yeah.net` 返回“请确认已开启 IMAP/SMTP 服务，并使用客户端授权码”。在 IMAP login 捕获处使用该函数。

- [ ] **步骤 3：检查前端提示**

检查 `ProfileForm.tsx` 是否已有授权码提示；没有则在 IMAP 密码附近添加：`QQ、163/126/yeah.net 邮箱请先开启 IMAP/SMTP，并填写客户端授权码。`

- [ ] **步骤 4：运行测试/检查**

运行：

```bash
cd backend && uv run python -m unittest test.test_mail_runtime
cd frontend && npm run lint
```

如果未改前端，可以跳过 `npm run lint`。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/mail_runtime.py backend/test/test_mail_runtime.py frontend/src/components/molecules/ProfileForm.tsx
git commit -m "feat: improve imap provider compatibility guidance"
```

未改前端时不要添加 `ProfileForm.tsx`。

---

### 任务 10：清理旧 72 小时轮询路径并回归

**文件：**
- 修改：`backend/app/services/mail_runtime.py`
- 修改：`backend/app/services/task_runtime.py`
- 测试：相关后端测试

- [ ] **步骤 1：搜索旧路径**

运行：

```bash
rg -n "fetch_recent_inbox_messages|IMAP_LOOKBACK_HOURS|imap_lookback_hours|RFC822 INTERNALDATE|SINCE" backend/app backend/test
```

预期：生产默认路径不再调用 `fetch_recent_inbox_messages`，不再使用 `SINCE 72h` 作为后台同步。

- [ ] **步骤 2：删除或隔离旧函数**

如果没有业务引用，删除 `fetch_recent_inbox_messages`；如果测试或诊断仍需保留，改名为 `_legacy_fetch_recent_inbox_messages`，并确保后台 poller 和手动刷新都不调用它。

- [ ] **步骤 3：运行重点测试**

运行：

```bash
cd backend && uv run python -m unittest test.test_imap_message_fetcher
cd backend && uv run python -m unittest test.test_imap_sync_models
cd backend && uv run python -m unittest test.test_imap_sync_runtime
cd backend && uv run python -m unittest test.test_concurrency_guards
cd backend && uv run python -m unittest test.test_mail_runtime
cd backend && uv run python -m unittest test.test_runtime_manager
```

预期：全部 PASS。

- [ ] **步骤 4：运行后端完整测试**

运行：`cd backend && uv run python -m unittest discover test`

预期：PASS。若出现无关既有失败，只记录失败项和原因，不顺手修无关问题。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/mail_runtime.py backend/app/services/task_runtime.py backend/test
git commit -m "refactor(backend): remove legacy imap lookback polling path"
```

---

## 最终验收

- [ ] `cd backend && uv run python -m unittest discover test` 通过。
- [ ] 如修改前端提示，`cd frontend && npm run lint` 通过。
- [ ] `rg -n "RFC822 INTERNALDATE|fetch_recent_inbox_messages|SINCE" backend/app` 不显示默认后台同步路径。
- [ ] 工作区刷新接口不会触发整个邮箱全量扫描。
- [ ] 163/QQ 登录失败提示包含授权码和 IMAP/SMTP 开启说明。
- [ ] 发现回复后，对应任务统一进入 `reply_detected`，包括取消和回收站任务。
- [ ] 已有 `email_logs` 非空字段不被覆盖，空字段可被补齐。


