# 材料删除引用断开策略实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 实现材料删除时的可断开引用策略，避免 `discovered`、`matched`、`review_required`、`send_failed` 等可处理状态阻塞材料删除，同时保护已批准、定时和发送中的任务。

**架构：** 后端在 `materials` 服务层定义材料引用处理策略，API 删除流程先原子检查阻止状态，再清理所有可断开引用，最后删除材料记录和物理文件。测试集中覆盖邮件任务、批量任务、软删除和混合状态场景。

**技术栈：** FastAPI、SQLAlchemy AsyncSession、SQLite 测试数据库、Python unittest、uv。

---

## 参考规格

- 规格文档：`docs/superpowers/specs/2026-05-19-material-deletion-reference-detach-design.md`
- 当前删除实现：`backend/app/api/materials.py`
- 当前状态集合：`backend/app/services/materials.py`
- 主要测试文件：`backend/test/test_api_endpoints.py`

## 文件结构

- 修改：`backend/app/services/materials.py`
  - 职责：定义材料是否可作为默认材料、文本提取，以及材料删除引用策略常量和状态辅助函数。
- 修改：`backend/app/api/materials.py`
  - 职责：实现 `DELETE /api/materials/{material_id}` 的原子检查、引用断开、草稿废弃、日志 metadata 和物理文件删除顺序。
- 修改：`backend/test/test_api_endpoints.py`
  - 职责：覆盖材料删除在不同邮件任务和批量任务状态下的行为。

## 实现口径

- 必须阻止删除的邮件任务状态：`generating_draft`、`approved`、`scheduled`、`sending`。
- 可直接断开或重置的邮件任务状态：`discovered`、`matched`、`draft_failed`、`review_required`、`sent`、`send_failed`、`reply_detected`、`canceled`。
- 可继续批量任务状态：`running`、`paused`，继续阻止删除。
- 可断开批量任务状态：`stopped`、`completed`、`expired`。
- 如果任一引用任务处于必须阻止状态，整个删除失败，不清理任何引用。
- 默认材料命中 `review_required` 或 `send_failed` 时，清空草稿和审核内容，状态回退到 `matched` 或 `discovered`。
- 仅随信材料命中 `review_required` 或 `send_failed` 时，移除附件，清空审核内容，状态统一为 `review_required`，保留 `generated_*`。
- 默认材料命中 `draft_failed` 时，清空 `last_error`，状态回退到 `matched` 或 `discovered`。
- 删除数据库记录提交成功后，再删除物理文件。

---
### 任务 1：补充邮件任务删除材料测试

**文件：**
- 修改：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：扩展测试辅助函数**

在 `ApiEndpointsTestCase` 的 `_insert_email_task_with_material` 附近增加可选字段参数，支持插入草稿、审核内容、匹配结果和定时字段。将函数签名改为：

```python
def _insert_email_task_with_material(
    self,
    *,
    identity_id: int,
    llm_id: int,
    professor_id: int,
    status: str,
    primary_material_id: int | None,
    selected_material_ids: list[int] | None = None,
    generated_subject: str | None = None,
    generated_content_text: str | None = None,
    generated_content_html: str | None = None,
    approved_subject: str | None = None,
    approved_body_text: str | None = None,
    approved_body_html: str | None = None,
    match_score: int | None = None,
    match_reason: str | None = None,
) -> int:
```

将 SQL 插入字段扩展为：

```sql
identity_id, llm_profile_id, professor_id,
status, primary_material_id, selected_material_ids, last_error,
generated_subject, generated_content_text, generated_content_html,
approved_subject, approved_body_text, approved_body_html,
approved_at, match_score, match_reason
```

`approved_at` 在任一 `approved_*` 字段非空时写入 `datetime('now')`，否则写入 `NULL`。

- [ ] **步骤 2：增加任务状态读取辅助函数**

在 `_get_task_material_references` 后新增：

```python
def _get_email_task_delete_state(self, task_id: int) -> dict[str, object | None]:
    connection = sqlite3.connect(self.db_path)
    try:
        row = connection.execute(
            """
            SELECT status, primary_material_id, selected_material_ids,
                   generated_subject, generated_content_text, generated_content_html,
                   approved_subject, approved_body_text, approved_body_html,
                   approved_at, last_error
            FROM email_tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
    finally:
        connection.close()
    return {
        "status": row[0],
        "primary_material_id": row[1],
        "selected_material_ids": json.loads(row[2]) if row[2] is not None else None,
        "generated_subject": row[3],
        "generated_content_text": row[4],
        "generated_content_html": row[5],
        "approved_subject": row[6],
        "approved_body_text": row[7],
        "approved_body_html": row[8],
        "approved_at": row[9],
        "last_error": row[10],
    }
```

- [ ] **步骤 3：编写 `discovered` / `matched` 默认材料断开测试**

在现有 `test_delete_material_clears_draft_failed_primary_material_reference` 前增加：

```python
def test_delete_material_detaches_discovered_and_matched_primary_material_references(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()
    material_id = self._upload_material(
        identity_id,
        filename="resume.txt",
        content=b"My research background is in information extraction.",
        material_type="resume",
    )
    discovered_professor_id = self._create_professor(email="discovered-material-delete@example.edu")
    matched_professor_id = self._create_professor(email="matched-material-delete@example.edu")
    discovered_task_id = self._insert_email_task_with_material(
        identity_id=identity_id,
        llm_id=llm_id,
        professor_id=discovered_professor_id,
        status="discovered",
        primary_material_id=material_id,
    )
    matched_task_id = self._insert_email_task_with_material(
        identity_id=identity_id,
        llm_id=llm_id,
        professor_id=matched_professor_id,
        status="matched",
        primary_material_id=material_id,
        match_score=82,
        match_reason="方向匹配",
    )

    delete_response = self.client.delete(f"/api/materials/{material_id}")

    self.assertEqual(delete_response.status_code, 204)
    self.assertEqual(self._get_email_task_delete_state(discovered_task_id)["status"], "discovered")
    self.assertEqual(self._get_email_task_delete_state(matched_task_id)["status"], "matched")
    self.assertIsNone(self._get_task_material_references(discovered_task_id)[0])
    self.assertIsNone(self._get_task_material_references(matched_task_id)[0])
```

- [ ] **步骤 4：编写 `review_required` 默认材料废弃草稿测试**

增加：

```python
def test_delete_material_resets_review_required_primary_material_draft(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()
    material_id = self._upload_material(
        identity_id,
        filename="resume.txt",
        content=b"My research background is in information extraction.",
        material_type="resume",
    )
    professor_id = self._create_professor(email="review-material-delete@example.edu")
    task_id = self._insert_email_task_with_material(
        identity_id=identity_id,
        llm_id=llm_id,
        professor_id=professor_id,
        status="review_required",
        primary_material_id=material_id,
        generated_subject="旧草稿主题",
        generated_content_text="旧草稿正文",
        generated_content_html="<p>旧草稿正文</p>",
        approved_subject="已审核主题",
        approved_body_text="已审核正文",
        approved_body_html="<p>已审核正文</p>",
        match_score=88,
        match_reason="方向匹配",
    )

    delete_response = self.client.delete(f"/api/materials/{material_id}")

    self.assertEqual(delete_response.status_code, 204)
    state = self._get_email_task_delete_state(task_id)
    self.assertEqual(state["status"], "matched")
    self.assertIsNone(state["primary_material_id"])
    self.assertIsNone(state["generated_subject"])
    self.assertIsNone(state["generated_content_text"])
    self.assertIsNone(state["generated_content_html"])
    self.assertIsNone(state["approved_subject"])
    self.assertIsNone(state["approved_body_text"])
    self.assertIsNone(state["approved_body_html"])
    self.assertIsNone(state["approved_at"])
```

- [ ] **步骤 5：编写 `review_required` 仅附件命中测试**

增加：

```python
def test_delete_material_removes_review_required_attachment_and_requires_review(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()
    deleted_material_id = self._upload_material(identity_id, filename="portfolio.pdf", content=b"portfolio", material_type="portfolio")
    remaining_material_id = self._upload_material(identity_id, filename="resume.txt", content=b"resume", material_type="resume")
    professor_id = self._create_professor(email="review-attachment-delete@example.edu")
    task_id = self._insert_email_task_with_material(
        identity_id=identity_id,
        llm_id=llm_id,
        professor_id=professor_id,
        status="review_required",
        primary_material_id=None,
        selected_material_ids=[deleted_material_id, remaining_material_id],
        generated_subject="保留草稿主题",
        generated_content_text="保留草稿正文",
        generated_content_html="<p>保留草稿正文</p>",
        approved_subject="清空审核主题",
        approved_body_text="清空审核正文",
        approved_body_html="<p>清空审核正文</p>",
    )

    delete_response = self.client.delete(f"/api/materials/{deleted_material_id}")

    self.assertEqual(delete_response.status_code, 204)
    state = self._get_email_task_delete_state(task_id)
    self.assertEqual(state["status"], "review_required")
    self.assertEqual(state["selected_material_ids"], [remaining_material_id])
    self.assertEqual(state["generated_subject"], "保留草稿主题")
    self.assertIsNone(state["approved_subject"])
    self.assertIsNone(state["approved_body_text"])
    self.assertIsNone(state["approved_body_html"])
    self.assertIsNone(state["approved_at"])
```

- [ ] **步骤 6：编写 `send_failed` 处理测试**

增加两个测试：

```python
def test_delete_material_resets_send_failed_primary_material(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()
    material_id = self._upload_material(identity_id, filename="resume.txt", content=b"resume", material_type="resume")
    professor_id = self._create_professor(email="send-failed-primary-delete@example.edu")
    task_id = self._insert_email_task_with_material(
        identity_id=identity_id,
        llm_id=llm_id,
        professor_id=professor_id,
        status="send_failed",
        primary_material_id=material_id,
        generated_subject="旧主题",
        generated_content_text="旧正文",
        generated_content_html="<p>旧正文</p>",
        approved_subject="发送失败主题",
        approved_body_text="发送失败正文",
        approved_body_html="<p>发送失败正文</p>",
    )

    delete_response = self.client.delete(f"/api/materials/{material_id}")

    self.assertEqual(delete_response.status_code, 204)
    state = self._get_email_task_delete_state(task_id)
    self.assertEqual(state["status"], "discovered")
    self.assertIsNone(state["primary_material_id"])
    self.assertIsNone(state["generated_subject"])
    self.assertIsNone(state["approved_subject"])
    self.assertIsNone(state["last_error"])


def test_delete_material_turns_send_failed_attachment_into_review_required(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()
    deleted_material_id = self._upload_material(identity_id, filename="attachment.pdf", content=b"attachment", material_type="portfolio")
    professor_id = self._create_professor(email="send-failed-attachment-delete@example.edu")
    task_id = self._insert_email_task_with_material(
        identity_id=identity_id,
        llm_id=llm_id,
        professor_id=professor_id,
        status="send_failed",
        primary_material_id=None,
        selected_material_ids=[deleted_material_id],
        generated_subject="保留主题",
        generated_content_text="保留正文",
        generated_content_html="<p>保留正文</p>",
        approved_subject="清空主题",
        approved_body_text="清空正文",
        approved_body_html="<p>清空正文</p>",
    )

    delete_response = self.client.delete(f"/api/materials/{deleted_material_id}")

    self.assertEqual(delete_response.status_code, 204)
    state = self._get_email_task_delete_state(task_id)
    self.assertEqual(state["status"], "review_required")
    self.assertEqual(state["selected_material_ids"], [])
    self.assertEqual(state["generated_subject"], "保留主题")
    self.assertIsNone(state["approved_subject"])
```

- [ ] **步骤 7：编写阻止状态原子失败测试**

增加：

```python
def test_delete_material_does_not_partially_detach_when_blocked_task_exists(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()
    material_id = self._upload_material(identity_id, filename="resume.txt", content=b"resume", material_type="resume")
    detachable_professor_id = self._create_professor(email="detachable-blocked-delete@example.edu")
    blocked_professor_id = self._create_professor(email="approved-blocked-delete@example.edu")
    detachable_task_id = self._insert_email_task_with_material(
        identity_id=identity_id,
        llm_id=llm_id,
        professor_id=detachable_professor_id,
        status="matched",
        primary_material_id=material_id,
    )
    blocked_task_id = self._insert_email_task_with_material(
        identity_id=identity_id,
        llm_id=llm_id,
        professor_id=blocked_professor_id,
        status="approved",
        primary_material_id=material_id,
    )

    delete_response = self.client.delete(f"/api/materials/{material_id}")

    self.assertEqual(delete_response.status_code, 400)
    self.assertEqual(delete_response.json()["detail"], "当前材料仍被已批准、定时或发送中的任务使用")
    self.assertEqual(self._get_task_material_references(detachable_task_id)[0], material_id)
    self.assertEqual(self._get_task_material_references(blocked_task_id)[0], material_id)
```

- [ ] **步骤 8：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_detaches_discovered_and_matched_primary_material_references test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_resets_review_required_primary_material_draft test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_removes_review_required_attachment_and_requires_review test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_resets_send_failed_primary_material test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_turns_send_failed_attachment_into_review_required test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_does_not_partially_detach_when_blocked_task_exists
```

预期：新增测试失败，至少 `discovered` / `matched` 删除返回 400，`review_required` 和 `send_failed` 未按新规则重置。

- [ ] **步骤 9：Commit 测试**

```powershell
git add backend/test/test_api_endpoints.py
git commit -m "test(backend): cover material reference detach states"
```

---
### 任务 2：实现材料引用策略常量

**文件：**
- 修改：`backend/app/services/materials.py`

- [ ] **步骤 1：替换旧状态集合**

将 `TERMINAL_MATERIAL_REFERENCING_STATUSES` 替换为：

``python
MATERIAL_REFERENCE_BLOCKING_STATUSES = {
    EmailTaskStatus.GENERATING_DRAFT.value,
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
    EmailTaskStatus.SENDING.value,
}

MATERIAL_REFERENCE_DETACHABLE_STATUSES = {
    EmailTaskStatus.DISCOVERED.value,
    EmailTaskStatus.MATCHED.value,
    EmailTaskStatus.DRAFT_FAILED.value,
    EmailTaskStatus.REVIEW_REQUIRED.value,
    EmailTaskStatus.SENT.value,
    EmailTaskStatus.SEND_FAILED.value,
    EmailTaskStatus.REPLY_DETECTED.value,
    EmailTaskStatus.CANCELED.value,
}

MATERIAL_REFERENCE_RESET_DRAFT_STATUSES = {
    EmailTaskStatus.REVIEW_REQUIRED.value,
    EmailTaskStatus.SEND_FAILED.value,
}
``

- [ ] **步骤 2：新增状态回退辅助函数**

在常量后新增：

``python
def material_reference_fallback_status(task) -> str:
    if (
        task.match_score is not None
        or bool(task.match_reason)
        or bool(task.fit_points)
        or bool(task.risk_points)
        or bool(task.match_keywords)
    ):
        return EmailTaskStatus.MATCHED.value
    return EmailTaskStatus.DISCOVERED.value
``

- [ ] **步骤 3：运行导入检查**

运行：`cd backend; uv run python -c "from app.services.materials import MATERIAL_REFERENCE_BLOCKING_STATUSES; print(sorted(MATERIAL_REFERENCE_BLOCKING_STATUSES))"`

预期：命令成功输出阻止状态列表。

- [ ] **步骤 4：Commit 常量**

``powershell
git add backend/app/services/materials.py
git commit -m "refactor(backend): define material reference detach policy"
``

---

### 任务 3：重写材料删除流程

**文件：**
- 修改：`backend/app/api/materials.py`

- [ ] **步骤 1：更新 import**

将旧的 `TERMINAL_MATERIAL_REFERENCING_STATUSES` import 改为：

``python
from app.services.materials import (
    MATERIAL_REFERENCE_BLOCKING_STATUSES,
    MATERIAL_REFERENCE_DETACHABLE_STATUSES,
    MATERIAL_REFERENCE_RESET_DRAFT_STATUSES,
    build_material_download_name,
    material_can_be_primary,
    material_reference_fallback_status,
)
``

- [ ] **步骤 2：新增引用判断和清理辅助函数**

在 `_normalize_material_type` 前新增：

``python
def _task_references_material(task: EmailTask, material_id: int) -> bool:
    return task.primary_material_id == material_id or material_id in (task.selected_material_ids or [])


def _batch_task_references_material(task: BatchTask, material_id: int) -> bool:
    return task.primary_material_id == material_id or material_id in (task.selected_material_ids or [])


def _clear_generated_draft(task: EmailTask) -> None:
    task.generated_subject = None
    task.generated_content_text = None
    task.generated_content_html = None


def _clear_approved_draft(task: EmailTask) -> None:
    task.approved_subject = None
    task.approved_body_text = None
    task.approved_body_html = None
    task.approved_at = None
    task.scheduled_at = None
``

- [ ] **步骤 3：新增邮件任务断开函数**

继续新增：

``python
def _detach_material_from_email_task(task: EmailTask, material_id: int) -> tuple[bool, bool, bool]:
    detached_primary = False
    removed_attachment = False
    reset_draft = False

    if task.primary_material_id == material_id:
        task.primary_material_id = None
        detached_primary = True
        if task.status in MATERIAL_REFERENCE_RESET_DRAFT_STATUSES:
            _clear_generated_draft(task)
            _clear_approved_draft(task)
            task.status = material_reference_fallback_status(task)
            task.last_error = None
            reset_draft = True
        elif task.status == EmailTaskStatus.DRAFT_FAILED.value:
            task.status = material_reference_fallback_status(task)
            task.last_error = None

    if material_id in (task.selected_material_ids or []):
        task.selected_material_ids = [item for item in task.selected_material_ids or [] if item != material_id]
        removed_attachment = True
        if not detached_primary and task.status in MATERIAL_REFERENCE_RESET_DRAFT_STATUSES:
            _clear_approved_draft(task)
            task.status = EmailTaskStatus.REVIEW_REQUIRED.value
            task.last_error = None
            reset_draft = True

    if detached_primary or removed_attachment or reset_draft:
        task.updated_at = datetime.now(UTC)

    return detached_primary, removed_attachment, reset_draft
``

- [ ] **步骤 4：新增批量任务断开函数**

继续新增：

``python
def _detach_material_from_batch_task(task: BatchTask, material_id: int) -> bool:
    updated = False
    if task.primary_material_id == material_id:
        task.primary_material_id = None
        updated = True
    if material_id in (task.selected_material_ids or []):
        task.selected_material_ids = [item for item in task.selected_material_ids or [] if item != material_id]
        updated = True
    if updated:
        task.updated_at = datetime.now(UTC)
    return updated
``

- [ ] **步骤 5：替换 `delete_material` 主流程**

实现顺序必须是：加载引用邮件任务 → 检查阻止状态 → 检查可继续批量任务 → 清理可断开邮件任务 → 清理不可继续批量任务 → 清当前身份默认材料 → 记录日志 → 删除材料记录 → 提交事务 → 删除物理文件。

阻止状态错误文案使用：`当前材料仍被已批准、定时或发送中的任务使用`。

日志 metadata 必须包含：`was_primary`、`detached_primary_task_ids`、`removed_attachment_task_ids`、`reset_draft_task_ids`、`detached_batch_task_ids`。

- [ ] **步骤 6：调整文件删除顺序**

保存 `material_file_path = material.file_path`，在 `await session.commit()` 成功后再调用 `delete_file(material_file_path)`。

- [ ] **步骤 7：运行新增测试**

运行任务 1 步骤 8 的测试命令。

预期：新增测试全部通过。

- [ ] **步骤 8：Commit 实现**

``powershell
git add backend/app/api/materials.py backend/app/services/materials.py
git commit -m "fix(backend): detach safe material references on delete"
``

---
### 任务 4：补充批量任务和回归测试

**文件：**
- 修改：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：更新现有失败态测试预期**

检查现有测试：

- `test_delete_material_clears_draft_failed_primary_material_reference`
- `test_delete_material_clears_send_failed_primary_material_reference`
- `test_delete_material_removes_failed_task_selected_material_reference`

确保新规则下预期一致：

- `draft_failed` 默认材料命中后状态应回退到 `matched` 或 `discovered`，并清空 `last_error`。
- `send_failed` 默认材料命中后状态应回退到 `matched` 或 `discovered`，并清空草稿、审核内容和 `last_error`。
- `send_failed` 仅附件命中后状态应变为 `review_required`，并清空审核内容。

- [ ] **步骤 2：增加同一材料同时作为默认和附件的测试**

新增测试：

``python
def test_delete_material_clears_primary_and_attachment_reference_together(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()
    material_id = self._upload_material(identity_id, filename="resume.txt", content=b"resume", material_type="resume")
    professor_id = self._create_professor(email="primary-and-attachment-delete@example.edu")
    task_id = self._insert_email_task_with_material(
        identity_id=identity_id,
        llm_id=llm_id,
        professor_id=professor_id,
        status="review_required",
        primary_material_id=material_id,
        selected_material_ids=[material_id],
        generated_subject="旧主题",
        generated_content_text="旧正文",
        generated_content_html="<p>旧正文</p>",
    )

    delete_response = self.client.delete(f"/api/materials/{material_id}")

    self.assertEqual(delete_response.status_code, 204)
    state = self._get_email_task_delete_state(task_id)
    self.assertEqual(state["status"], "discovered")
    self.assertIsNone(state["primary_material_id"])
    self.assertEqual(state["selected_material_ids"], [])
    self.assertIsNone(state["generated_subject"])
``

- [ ] **步骤 3：确认不可继续批量任务可断开**

运行现有测试：

``powershell
cd backend
uv run python -m unittest test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_clears_stopped_batch_task_material_reference test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_still_blocks_running_batch_task_reference
``

预期：两个测试通过。`stopped` 清理引用，`running` 继续阻止删除。

- [ ] **步骤 4：运行材料删除相关测试集合**

运行：

``powershell
cd backend
uv run python -m unittest test.test_api_endpoints.ApiEndpointsTestCase.test_material_upload_open_download_set_primary_and_delete test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_detaches_discovered_and_matched_primary_material_references test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_resets_review_required_primary_material_draft test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_removes_review_required_attachment_and_requires_review test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_resets_send_failed_primary_material test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_turns_send_failed_attachment_into_review_required test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_clears_draft_failed_primary_material_reference test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_clears_send_failed_primary_material_reference test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_removes_failed_task_selected_material_reference test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_clears_stopped_batch_task_material_reference test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_still_blocks_running_batch_task_reference test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_does_not_partially_detach_when_blocked_task_exists test.test_api_endpoints.ApiEndpointsTestCase.test_delete_material_clears_primary_and_attachment_reference_together
``

预期：全部 PASS。

- [ ] **步骤 5：Commit 回归测试**

``powershell
git add backend/test/test_api_endpoints.py
git commit -m "test(backend): verify material delete regression cases"
``

---

### 任务 5：全量验证和文档对齐

**文件：**
- 修改：`docs/superpowers/specs/2026-05-19-material-deletion-reference-detach-design.md`（仅当实现口径发现必须同步说明时）
- 修改：`docs/superpowers/plans/2026-05-19-material-deletion-reference-detach-plan.md`（执行时勾选进度）

- [ ] **步骤 1：运行后端 API 测试文件**

运行：

``powershell
cd backend
uv run python -m unittest test.test_api_endpoints
``

预期：测试全部通过。

- [ ] **步骤 2：搜索旧常量引用**

运行：

``powershell
rg -n "TERMINAL_MATERIAL_REFERENCING_STATUSES" backend
``

预期：无输出。如果有输出，改为新常量或删除旧引用。

- [ ] **步骤 3：检查材料删除文件顺序**

人工检查 `backend/app/api/materials.py`，确认 `delete_file(material_file_path)` 位于 `await session.commit()` 之后。

- [ ] **步骤 4：检查规格覆盖度**

逐项对照规格文档，确认以下内容都已实现或明确延期：

- 可断开状态覆盖 `discovered`、`matched`、`draft_failed`、`review_required`、`sent`、`send_failed`、`reply_detected`、`canceled`。
- 阻止状态覆盖 `generating_draft`、`approved`、`scheduled`、`sending`。
- 阻止状态存在时整体失败，无部分清理。
- `review_required` 和 `send_failed` 按默认材料/随信材料分别处理。
- 批量任务 `running`、`paused` 阻止，`stopped`、`completed`、`expired` 可断开。
- 操作日志 metadata 包含被影响任务 ID。

- [ ] **步骤 5：最终 Commit**

如果任务 5 只勾选计划进度且没有代码变化，不需要 commit。如果同步修改了文档，运行：

``powershell
git add docs/superpowers/specs/2026-05-19-material-deletion-reference-detach-design.md docs/superpowers/plans/2026-05-19-material-deletion-reference-detach-plan.md
git commit -m "docs: align material deletion implementation plan"
``

---

## 执行注意事项

- 不要新增「放弃草稿」按钮；本计划通过删除材料时自动断开和重置来解决问题。
- 不要把 `review_required` 简单加入终态集合后只清引用；必须废弃草稿或审核内容。
- 不要在有阻止状态时清理部分任务；这是原子性要求。
- 不要在数据库提交前删除物理文件。
- 不要修改无关任务状态机和前端发送流程。

## 计划自检

- 规格覆盖度：本计划覆盖状态矩阵、草稿废弃、软删除可断开、原子性、日志 metadata、文件删除顺序和测试验收。
- 占位符扫描：无「待定」「TODO」「后续实现」占位步骤；每个实现步骤包含具体文件、代码或命令。
- 类型一致性：新增常量、辅助函数和测试 helper 命名在任务之间保持一致。
