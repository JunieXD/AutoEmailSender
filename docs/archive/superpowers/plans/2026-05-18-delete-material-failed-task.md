# 失败任务引用材料时允许删除材料 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 允许删除仅被失败邮件任务引用的材料，并在删除时清理失败任务中的材料引用。

**架构：** 后端材料删除接口继续负责删除前引用保护。将 `draft_failed`、`send_failed` 纳入“不会阻止材料删除”的状态集合，同时在删除材料前清理这些失败/终态任务上的 `primary_material_id` 与 `selected_material_ids`，避免悬挂引用和 JSON 残留。

**技术栈：** FastAPI、SQLAlchemy AsyncSession、SQLite、Python unittest、uv。

---

## 文件结构

- 修改：`backend/app/services/materials.py`
  - 职责：集中定义“不会阻止材料删除”的邮件任务状态集合。
- 修改：`backend/app/api/materials.py`
  - 职责：执行材料删除前的阻塞校验、失败任务引用清理、材料记录与文件删除。
- 修改：`backend/test/test_api_endpoints.py`
  - 职责：通过接口级测试锁定材料删除行为，覆盖失败任务可删除、进行中任务仍阻止、JSON 随信材料清理。

## 任务 1：为失败默认材料引用编写红灯测试

**文件：**
- 修改：`backend/test/test_api_endpoints.py`
- 测试：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：添加测试辅助方法**

在 `ApiEndpointTests` 类的 `_upload_material` 方法之前添加以下辅助方法，用于创建测试导师、直接插入邮件任务、读取任务材料引用：

```python
    def _create_professor(self, *, email: str = "professor@example.edu") -> int:
        response = self.client.post(
            "/api/professors",
            json={
                "name": "材料删除测试导师",
                "email": email,
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Agents",
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return response.json()["id"]

    def _insert_email_task_with_material(
        self,
        *,
        identity_id: int,
        llm_id: int,
        professor_id: int,
        status: str,
        primary_material_id: int | None,
        selected_material_ids: list[int] | None = None,
    ) -> int:
        connection = sqlite3.connect(self.db_path)
        try:
            task_id = connection.execute(
                """
                INSERT INTO email_tasks (
                    identity_id, llm_profile_id, professor_id,
                    status, primary_material_id, selected_material_ids, last_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    identity_id,
                    llm_id,
                    professor_id,
                    status,
                    primary_material_id,
                    json.dumps(selected_material_ids) if selected_material_ids is not None else None,
                    "失败任务错误" if status in {"draft_failed", "send_failed"} else None,
                ),
            ).fetchone()[0]
            connection.commit()
        finally:
            connection.close()
        return task_id

    def _get_task_material_references(self, task_id: int) -> tuple[int | None, list[int] | None]:
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT primary_material_id, selected_material_ids FROM email_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        finally:
            connection.close()
        selected_material_ids = json.loads(row[1]) if row[1] is not None else None
        return row[0], selected_material_ids
```

- [ ] **步骤 2：添加 `draft_failed` 默认材料删除测试**

在 `test_material_upload_open_download_set_primary_and_delete` 后添加：

```python
    def test_delete_material_clears_draft_failed_primary_material_reference(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research background is in information extraction.",
            material_type="resume",
        )
        professor_id = self._create_professor(email="draft-failed-material-delete@example.edu")
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="draft_failed",
            primary_material_id=material_id,
        )

        delete_response = self.client.delete(f"/api/materials/{material_id}")

        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)
        primary_material_id, selected_material_ids = self._get_task_material_references(task_id)
        self.assertIsNone(primary_material_id)
        self.assertIsNone(selected_material_ids)
```

- [ ] **步骤 3：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_delete_material_clears_draft_failed_primary_material_reference
```

预期：FAIL，状态码为 400，响应包含“当前材料仍被未完成任务作为默认材料使用”。

- [ ] **步骤 4：Commit 红灯测试**

```bash
git add backend/test/test_api_endpoints.py
git commit -m "test(backend): cover deleting material used by failed task"
```

## 任务 2：实现失败状态不阻止删除并清理默认材料引用

**文件：**
- 修改：`backend/app/services/materials.py`
- 修改：`backend/app/api/materials.py`
- 测试：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：扩大非阻塞状态集合**

将 `backend/app/services/materials.py` 中的 `TERMINAL_MATERIAL_REFERENCING_STATUSES` 改为：

```python
TERMINAL_MATERIAL_REFERENCING_STATUSES = {
    EmailTaskStatus.DRAFT_FAILED.value,
    EmailTaskStatus.SENT.value,
    EmailTaskStatus.SEND_FAILED.value,
    EmailTaskStatus.REPLY_DETECTED.value,
    EmailTaskStatus.CANCELED.value,
}
```

- [ ] **步骤 2：在删除材料前清理非阻塞任务默认材料引用**

在 `backend/app/api/materials.py` 的 `delete_material` 中，`for task in active_tasks:` 循环之后、`if is_current_primary:` 之前插入：

```python
    referencing_tasks = list(
        (
            await session.execute(
                select(EmailTask).where(
                    EmailTask.identity_id == material.identity_id,
                    EmailTask.status.in_(TERMINAL_MATERIAL_REFERENCING_STATUSES),
                ),
            )
        ).scalars()
    )
    for task in referencing_tasks:
        if task.primary_material_id == material.id:
            task.primary_material_id = None
            task.updated_at = datetime.now(UTC)
```

- [ ] **步骤 3：运行任务 1 测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_delete_material_clears_draft_failed_primary_material_reference
```

预期：PASS。

- [ ] **步骤 4：Commit 默认材料引用修复**

```bash
git add backend/app/services/materials.py backend/app/api/materials.py
git commit -m "fix(backend): allow failed tasks to release deleted primary material"
```

## 任务 3：覆盖 `send_failed` 和随信材料清理

**文件：**
- 修改：`backend/test/test_api_endpoints.py`
- 修改：`backend/app/api/materials.py`
- 测试：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：添加 `send_failed` 默认材料测试**

在 `test_delete_material_clears_draft_failed_primary_material_reference` 后添加：

```python
    def test_delete_material_clears_send_failed_primary_material_reference(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research background is in information extraction.",
            material_type="resume",
        )
        professor_id = self._create_professor(email="send-failed-material-delete@example.edu")
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="send_failed",
            primary_material_id=material_id,
        )

        delete_response = self.client.delete(f"/api/materials/{material_id}")

        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)
        primary_material_id, selected_material_ids = self._get_task_material_references(task_id)
        self.assertIsNone(primary_material_id)
        self.assertIsNone(selected_material_ids)
```

- [ ] **步骤 2：添加失败任务随信材料测试**

继续添加：

```python
    def test_delete_material_removes_failed_task_selected_material_reference(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        deleted_material_id = self._upload_material(
            identity_id,
            filename="portfolio.pdf",
            content=b"Portfolio content",
            material_type="portfolio",
        )
        remaining_material_id = self._upload_material(
            identity_id,
            filename="transcript.pdf",
            content=b"Transcript content",
            material_type="transcript",
        )
        professor_id = self._create_professor(email="failed-selected-material-delete@example.edu")
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="draft_failed",
            primary_material_id=None,
            selected_material_ids=[deleted_material_id, remaining_material_id],
        )

        delete_response = self.client.delete(f"/api/materials/{deleted_material_id}")

        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)
        primary_material_id, selected_material_ids = self._get_task_material_references(task_id)
        self.assertIsNone(primary_material_id)
        self.assertEqual(selected_material_ids, [remaining_material_id])
```

- [ ] **步骤 3：运行新增测试验证随信材料测试失败**

运行：

```bash
cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_delete_material_clears_send_failed_primary_material_reference test.test_api_endpoints.ApiEndpointTests.test_delete_material_removes_failed_task_selected_material_reference
```

预期：`send_failed` 默认材料测试 PASS；随信材料测试 FAIL，`selected_material_ids` 仍包含被删除材料。

- [ ] **步骤 4：实现随信材料引用清理**

将任务 2 中 `for task in referencing_tasks:` 循环扩展为：

```python
    for task in referencing_tasks:
        task_updated = False
        if task.primary_material_id == material.id:
            task.primary_material_id = None
            task_updated = True
        if material.id in (task.selected_material_ids or []):
            task.selected_material_ids = [
                selected_material_id
                for selected_material_id in task.selected_material_ids or []
                if selected_material_id != material.id
            ]
            task_updated = True
        if task_updated:
            task.updated_at = datetime.now(UTC)
```

- [ ] **步骤 5：运行任务 3 测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_delete_material_clears_send_failed_primary_material_reference test.test_api_endpoints.ApiEndpointTests.test_delete_material_removes_failed_task_selected_material_reference
```

预期：PASS。

- [ ] **步骤 6：Commit 随信材料引用清理**

```bash
git add backend/app/api/materials.py backend/test/test_api_endpoints.py
git commit -m "fix(backend): clear failed task selected material on delete"
```

## 任务 4：保护进行中任务阻止删除的回归测试

**文件：**
- 修改：`backend/test/test_api_endpoints.py`
- 测试：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：添加进行中默认材料阻止测试**

在材料删除测试组后添加：

```python
    def test_delete_material_still_blocks_active_primary_material_reference(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research background is in information extraction.",
            material_type="resume",
        )
        professor_id = self._create_professor(email="active-primary-material-delete@example.edu")
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="review_required",
            primary_material_id=material_id,
        )

        delete_response = self.client.delete(f"/api/materials/{material_id}")

        self.assertEqual(delete_response.status_code, 400)
        self.assertEqual(delete_response.json()["detail"], "当前材料仍被未完成任务作为默认材料使用")
        primary_material_id, selected_material_ids = self._get_task_material_references(task_id)
        self.assertEqual(primary_material_id, material_id)
        self.assertIsNone(selected_material_ids)
```

- [ ] **步骤 2：添加进行中随信材料阻止测试**

继续添加：

```python
    def test_delete_material_still_blocks_active_selected_material_reference(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="portfolio.pdf",
            content=b"Portfolio content",
            material_type="portfolio",
        )
        professor_id = self._create_professor(email="active-selected-material-delete@example.edu")
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="approved",
            primary_material_id=None,
            selected_material_ids=[material_id],
        )

        delete_response = self.client.delete(f"/api/materials/{material_id}")

        self.assertEqual(delete_response.status_code, 400)
        self.assertEqual(delete_response.json()["detail"], "当前材料仍被未完成任务选为随信材料")
        primary_material_id, selected_material_ids = self._get_task_material_references(task_id)
        self.assertIsNone(primary_material_id)
        self.assertEqual(selected_material_ids, [material_id])
```

- [ ] **步骤 3：运行保护测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_delete_material_still_blocks_active_primary_material_reference test.test_api_endpoints.ApiEndpointTests.test_delete_material_still_blocks_active_selected_material_reference
```

预期：PASS。

- [ ] **步骤 4：Commit 保护测试**

```bash
git add backend/test/test_api_endpoints.py
git commit -m "test(backend): keep active material delete guards"
```

## 任务 5：聚焦验证与最终清理

**文件：**
- 检查：`backend/app/services/materials.py`
- 检查：`backend/app/api/materials.py`
- 检查：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：运行材料删除相关测试**

运行：

```bash
cd backend && uv run python -m unittest \
  test.test_api_endpoints.ApiEndpointTests.test_material_upload_open_download_set_primary_and_delete \
  test.test_api_endpoints.ApiEndpointTests.test_delete_material_clears_draft_failed_primary_material_reference \
  test.test_api_endpoints.ApiEndpointTests.test_delete_material_clears_send_failed_primary_material_reference \
  test.test_api_endpoints.ApiEndpointTests.test_delete_material_removes_failed_task_selected_material_reference \
  test.test_api_endpoints.ApiEndpointTests.test_delete_material_still_blocks_active_primary_material_reference \
  test.test_api_endpoints.ApiEndpointTests.test_delete_material_still_blocks_active_selected_material_reference
```

预期：全部 PASS。

- [ ] **步骤 2：运行后端接口测试文件**

运行：

```bash
cd backend && uv run python -m unittest test.test_api_endpoints
```

预期：全部 PASS。如出现无关失败，记录失败用例、错误信息和为何判断无关，不要扩大本次修复范围。

- [ ] **步骤 3：检查工作区 diff**

运行：

```bash
git diff -- backend/app/services/materials.py backend/app/api/materials.py backend/test/test_api_endpoints.py
```

预期：diff 只包含失败任务材料删除行为、引用清理和相关测试。

- [ ] **步骤 4：最终 Commit**

如果任务 1-4 的 commit 未执行，则一次性提交：

```bash
git add backend/app/services/materials.py backend/app/api/materials.py backend/test/test_api_endpoints.py
git commit -m "fix(backend): allow deleting materials used only by failed tasks"
```

如果任务 1-4 已逐步 commit，则本步骤无需新 commit，只记录最终测试结果。
