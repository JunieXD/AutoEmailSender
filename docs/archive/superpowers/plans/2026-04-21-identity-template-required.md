# Identity Template Required Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make identity-level套磁信模板在保存前必填，并允许用户在首次保存身份前先导入模板文件，再手动补齐主题。

**Architecture:** 保持当前“身份级一套模板字段，模板润色与固定模板共用”的数据结构不变，把校验中心前移到身份编辑页，同时在后端保留统一兜底。新增一个不依赖 `identity_id` 的模板导入接口，用同一套模板解析逻辑支持“新建身份先导入、后保存”的流程。

**Tech Stack:** React 19, Vite, TypeScript, FastAPI, SQLAlchemy, Pydantic, unittest, uv

---

## File Map

- Modify: `backend/app/services/outreach_templates.py`
  负责抽出“主题 + 纯文本正文是否完整”的共享判断函数，并继续保留模板导入时 HTML 自动转纯文本的能力。
- Modify: `backend/app/api/identities.py`
  新增通用模板导入接口 `POST /api/identities/template-import`，并把身份创建/更新校验改成无论哪种模式都必须有主题和纯文本正文。
- Modify: `backend/app/api/batch_tasks.py`
  对解析后的模板快照做兜底校验，防止历史脏数据通过批量任务入口漏过去。
- Modify: `backend/app/services/task_runtime.py`
  在生成草稿前校验任务快照 + 身份回退模板是否仍然完整，阻止历史空模板继续用默认主题静默发送。
- Modify: `backend/test/test_api_endpoints.py`
  增加“未保存身份也能导入模板”“身份保存必须有主题和纯文本正文”“历史脏数据仍会被任务入口拦截”的接口回归测试。
- Modify: `backend/test/test_outreach_templates.py`
  锁定模板导入契约：导入结果 `subject` 仍然为空，`body_text` 由导入内容自动生成。
- Modify: `frontend/src/lib/api/identities.ts`
  把模板导入改为调用新的通用接口，不再要求先有身份 id。
- Modify: `frontend/src/pages/ProfilePage.tsx`
  允许新建身份时先导入模板，给主题和纯文本正文加必填语义，统一保存拦截和导入成功提示。
- No migration:
  数据库字段已经存在，不需要新增表或 Alembic migration。

### Task 1: Lock the backend contract with failing tests

**Files:**
- Modify: `backend/test/test_api_endpoints.py`
- Modify: `backend/test/test_outreach_templates.py`

- [ ] **Step 1: Add API tests for pre-save import and stricter identity validation**

```python
    def test_identity_template_import_endpoint_supports_unsaved_identity_flow(self) -> None:
        response = self.client.post(
            "/api/identities/template-import",
            files={
                "file": (
                    "template.html",
                    "<p>{{name}}老师您好，</p><p>我是{{sender_name}}。</p>".encode("utf-8"),
                    "text/html",
                ),
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertIsNone(payload["subject"])
        self.assertEqual(payload["format_name"], "html")
        self.assertEqual(payload["body_text"], "{{name}}老师您好，\n\n我是{{sender_name}}。")
        self.assertIn("<p>{{name}}老师您好，</p>", payload["body_html"])

    def test_identity_requires_template_subject_in_all_modes(self) -> None:
        for mode in ("llm", "template"):
            response = self.client.post(
                "/api/identities",
                json=self._build_identity_payload(
                    with_imap=False,
                    outreach_generation_mode=mode,
                    outreach_template_subject=None,
                    outreach_template_body_text="老师您好，我是{{sender_name}}。",
                    outreach_template_body_html="<p>老师您好，我是{{sender_name}}。</p>",
                ),
            )

            self.assertEqual(response.status_code, 400, msg=response.text)
            self.assertEqual(response.json()["detail"], "请先填写默认套磁信主题")

    def test_identity_requires_plain_text_template_body_even_when_html_exists(self) -> None:
        response = self.client.post(
            "/api/identities",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="template",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text=None,
                outreach_template_body_html="<p>老师您好，我是{{sender_name}}。</p>",
            ),
        )

        self.assertEqual(response.status_code, 400, msg=response.text)
        self.assertEqual(response.json()["detail"], "请先填写默认套磁信纯文本正文")

    def test_identity_update_requires_subject_and_plain_text_body(self) -> None:
        identity_id = self._create_identity(with_imap=False)

        response = self.client.put(
            f"/api/identities/{identity_id}",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="llm",
                outreach_template_subject=None,
                outreach_template_body_text=None,
                outreach_template_body_html="<p>老师您好，我是{{sender_name}}。</p>",
            ),
        )

        self.assertEqual(response.status_code, 400, msg=response.text)
        self.assertEqual(response.json()["detail"], "请先填写默认套磁信主题和纯文本正文")
```

- [ ] **Step 2: Rewrite legacy regression tests so they simulate stored invalid data instead of creating invalid identities**

```python
    def test_template_polish_mode_requires_complete_template_when_creating_batch_task(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE identity_profiles
                SET outreach_template_subject = NULL,
                    outreach_template_body_text = NULL,
                    outreach_template_body_html = NULL
                WHERE id = ?
                """,
                (identity_id,),
            )
            connection.commit()
        finally:
            connection.close()

        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My background covers information extraction.",
            material_type="resume",
        )
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "模板缺失批量任务",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": material_id,
                "email_subject": None,
                "email_body": None,
                "selected_material_ids": None,
                "outreach_generation_mode": "llm",
                "outreach_template_subject": None,
                "outreach_template_body_text": None,
                "outreach_template_body_html": None,
            },
        )

        self.assertEqual(response.status_code, 400, msg=response.text)
        self.assertEqual(response.json()["detail"], "请先填写默认套磁信主题和纯文本正文")

    def test_llm_mode_requires_complete_template_before_generating_draft(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research focuses on information extraction and agents.",
            material_type="resume",
        )
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]

        ensure_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(ensure_response.status_code, 200, msg=ensure_response.text)
        task_id = ensure_response.json()["current_task"]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE identity_profiles
                SET outreach_template_subject = NULL,
                    outreach_template_body_text = NULL,
                    outreach_template_body_html = NULL
                WHERE id = ?
                """,
                (identity_id,),
            )
            connection.execute(
                """
                UPDATE email_tasks
                SET outreach_generation_mode = ?,
                    outreach_template_subject = NULL,
                    outreach_template_body_text = NULL,
                    outreach_template_body_html = NULL
                WHERE id = ?
                """,
                ("llm", task_id),
            )
            connection.commit()
        finally:
            connection.close()

        with patch(
            "app.services.task_runtime.llm_runtime.generate_draft_content",
            AsyncMock(
                return_value=self._build_draft_generation_result(
                    subject="测试草稿",
                    body_text="测试正文",
                    body_html="<p>测试正文</p>",
                ),
            ),
        ) as mocked_generate:
            generate_response = self.client.post(f"/api/email-tasks/{task_id}/generate-draft")

        self.assertEqual(generate_response.status_code, 400)
        self.assertEqual(generate_response.json()["detail"], "请先填写默认套磁信主题和纯文本正文")
        mocked_generate.assert_not_awaited()

    def test_batch_task_outreach_snapshot_is_independent_from_identity_defaults(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        self.client.put(
            f"/api/identities/{identity_id}",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="llm",
                outreach_template_subject="默认主题 {{name}}",
                outreach_template_body_text="默认正文 {{name}}",
                outreach_template_body_html="<p>默认正文 {{name}}</p>",
            ),
        )

        # existing batch task creation stays unchanged

        self.client.put(
            f"/api/identities/{identity_id}",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="llm",
                outreach_template_subject="后来改掉的主题",
                outreach_template_body_text="后来改掉的正文",
                outreach_template_body_html="<p>后来改掉的正文</p>",
            ),
        )

        # existing workspace assertions stay unchanged
```

- [ ] **Step 3: Lock the import service contract that `subject` stays empty while `body_text` is derived**

```python
        self.assertEqual(result.format_name, "docx")
        self.assertIsNone(result.subject)
        self.assertEqual(result.body_html, converted_html)
        self.assertEqual(
            result.body_text,
            "老师您好：\n\n我是张三，想向您请教科研方向。\n\n已附上简历\n\n期待交流",
        )
```

- [ ] **Step 4: Run the targeted backend tests and confirm they fail for the expected reasons**

Run:

```bash
cd backend
uv run python -m unittest \
  test.test_api_endpoints.ApiEndpointTests.test_identity_template_import_endpoint_supports_unsaved_identity_flow \
  test.test_api_endpoints.ApiEndpointTests.test_identity_requires_template_subject_in_all_modes \
  test.test_api_endpoints.ApiEndpointTests.test_identity_requires_plain_text_template_body_even_when_html_exists \
  test.test_api_endpoints.ApiEndpointTests.test_identity_update_requires_subject_and_plain_text_body \
  test.test_api_endpoints.ApiEndpointTests.test_template_polish_mode_requires_complete_template_when_creating_batch_task \
  test.test_api_endpoints.ApiEndpointTests.test_llm_mode_requires_complete_template_before_generating_draft \
  test.test_outreach_templates.OutreachTemplateImportTests.test_docx_import_prefers_html_and_derives_plain_text_from_html \
  -v
```

Expected:

```text
FAIL: /api/identities/template-import returns 404
FAIL: identity create/update still return 201 or 200 for missing subject/body_text
FAIL: batch-task and draft-generation endpoints still return old body-only errors
```

- [ ] **Step 5: Commit the test-only red phase**

```bash
git add backend/test/test_api_endpoints.py backend/test/test_outreach_templates.py
git commit -m "test(backend): lock required identity template contract"
```

### Task 2: Implement backend support for pre-save import and complete-template validation

**Files:**
- Modify: `backend/app/services/outreach_templates.py`
- Modify: `backend/app/api/identities.py`
- Modify: `backend/app/api/batch_tasks.py`
- Modify: `backend/app/services/task_runtime.py`

- [ ] **Step 1: Add a shared helper that only treats subject + plain-text body as valid**

```python
def get_required_template_detail(
    subject_template: str | None,
    body_text_template: str | None,
) -> str | None:
    has_subject = bool((subject_template or "").strip())
    has_body_text = bool((body_text_template or "").strip())

    if has_subject and has_body_text:
        return None
    if not has_subject and not has_body_text:
        return "请先填写默认套磁信主题和纯文本正文"
    if not has_subject:
        return "请先填写默认套磁信主题"
    return "请先填写默认套磁信纯文本正文"
```

- [ ] **Step 2: Add a generic `POST /api/identities/template-import` endpoint and reuse one parsing helper for both routes**

```python
@router.post("/template-import", response_model=IdentityTemplateImportResult)
async def import_identity_template_for_unsaved_identity(
    file: UploadFile = File(...),
) -> IdentityTemplateImportResult:
    return await _parse_template_upload(file)


@router.post("/{identity_id}/template-import", response_model=IdentityTemplateImportResult)
async def import_identity_template(
    identity_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_async_session),
) -> IdentityTemplateImportResult:
    await _get_identity(session, identity_id)
    return await _parse_template_upload(file)


async def _parse_template_upload(file: UploadFile) -> IdentityTemplateImportResult:
    if not file.filename:
        raise HTTPException(status_code=400, detail="请选择模板文件")
    try:
        imported = import_outreach_template_file(file.filename, await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return IdentityTemplateImportResult(
        subject=imported.subject,
        body_text=imported.body_text,
        body_html=imported.body_html,
        format_name=imported.format_name,
    )
```

- [ ] **Step 3: Make identity create/update always require the shared subject + plain-text body contract**

```python
from app.services.outreach_templates import (
    OUTREACH_GENERATION_MODE_LLM,
    OUTREACH_GENERATION_MODE_TEMPLATE,
    get_required_template_detail,
    import_outreach_template_file,
)


def _validate_identity_outreach_defaults(data: dict[str, object]) -> None:
    detail = get_required_template_detail(
        data.get("outreach_template_subject"),
        data.get("outreach_template_body_text"),
    )
    if detail:
        raise HTTPException(status_code=400, detail=detail)
```

- [ ] **Step 4: Tighten batch-task creation and draft generation so legacy incomplete templates are rejected instead of silently falling back**

```python
# backend/app/api/batch_tasks.py
detail = get_required_template_detail(
    outreach_config.subject_template,
    outreach_config.body_text_template,
)
if detail:
    raise HTTPException(status_code=400, detail=detail)


# backend/app/services/task_runtime.py
template_subject = (
    _normalize_nullable_text(batch_task.email_subject) if batch_task else None
) or _normalize_nullable_text(outreach_config.subject_template)
template_body = (
    _normalize_nullable_text(batch_task.email_body) if batch_task else None
) or _normalize_nullable_text(outreach_config.body_text_template)
detail = get_required_template_detail(template_subject, template_body)
if detail:
    raise ValueError(detail)
```

- [ ] **Step 5: Run the full backend regression slice and confirm it passes**

Run:

```bash
cd backend
uv run python -m unittest test.test_api_endpoints test.test_outreach_templates -v
```

Expected:

```text
OK
```

- [ ] **Step 6: Commit the backend implementation**

```bash
git add backend/app/services/outreach_templates.py backend/app/api/identities.py backend/app/api/batch_tasks.py backend/app/services/task_runtime.py backend/test/test_api_endpoints.py backend/test/test_outreach_templates.py
git commit -m "feat(backend): require complete identity outreach templates"
```

### Task 3: Update the identity editor so import works before save and required fields are obvious

**Files:**
- Modify: `frontend/src/lib/api/identities.ts`
- Modify: `frontend/src/pages/ProfilePage.tsx`

- [ ] **Step 1: Point the frontend import helper at the new generic endpoint**

```ts
export const importIdentityTemplate = async (file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  return apiFetch<IdentityTemplateImportResultDTO>('/api/identities/template-import', {
    method: 'POST',
    body: formData,
  });
};
```

- [ ] **Step 2: Add one frontend helper for the required-template message and use it in `saveIdentity`**

```ts
const getIdentityTemplateValidationMessage = (
  form: Pick<IdentityFormState, "outreach_template_subject" | "outreach_template_body_text">,
) => {
  const hasSubject = Boolean(form.outreach_template_subject.trim());
  const hasBodyText = Boolean(form.outreach_template_body_text.trim());

  if (!hasSubject && !hasBodyText) {
    return "请先填写默认套磁信主题和纯文本正文";
  }
  if (!hasSubject) {
    return "请先填写默认套磁信主题";
  }
  if (!hasBodyText) {
    return "请先填写默认套磁信纯文本正文";
  }
  return null;
};

const saveIdentity = async () => {
  if (
    !identityForm.name.trim() ||
    !identityForm.email_address.trim() ||
    !identityForm.smtp_host.trim() ||
    !identityForm.smtp_password.trim() ||
    !identityForm.imap_host.trim() ||
    !identityForm.imap_port.trim()
  ) {
    setIdentityMessage("请先填写所有带红色星号的身份必填项");
    return;
  }

  const templateValidationMessage = getIdentityTemplateValidationMessage(identityForm);
  if (templateValidationMessage) {
    setIdentityMessage(templateValidationMessage);
    return;
  }

  // existing submit logic stays here
};
```

- [ ] **Step 3: Remove the save-first import gate and make the import success message explicitly tell the user that only正文 was auto-filled**

```ts
const handleTemplateFileImport = async (file: File) => {
  setImportingTemplateFile(true);
  try {
    const imported = await importIdentityTemplate(file);
    setIdentityForm((previous) => ({
      ...previous,
      outreach_template_subject:
        imported.subject ?? previous.outreach_template_subject,
      outreach_template_body_text: imported.body_text,
      outreach_template_body_html: imported.body_html,
    }));
    setIdentityMessage(
      identityForm.outreach_template_subject.trim()
        ? `已导入 ${imported.format_name} 模板文件，并自动生成纯文本正文。`
        : `已导入 ${imported.format_name} 模板文件，并自动生成纯文本正文。请继续填写模板主题后再保存身份。`,
    );
  } catch (importError) {
    setIdentityMessage(
      importError instanceof Error ? importError.message : "导入模板文件失败",
    );
  } finally {
    setImportingTemplateFile(false);
  }
};
```

- [ ] **Step 4: Update the summary card and modal copy so users can see that subject + plain-text body are required and import is always available**

```tsx
<div className="mt-1 text-xs leading-6 text-stone-500">
  这里设置的是身份级默认模板。保存身份前必须补齐主题和纯文本正文；你可以直接手填，也可以先导入模板文件再补主题。
</div>

<span className="rounded-full border border-red-200 bg-red-50 px-3 py-1 text-xs text-red-600">
  {hasSubject ? "已填写必填主题" : "缺少必填主题"}
</span>
<span className="rounded-full border border-red-200 bg-red-50 px-3 py-1 text-xs text-red-600">
  {hasTextTemplate ? "已填写必填纯文本正文" : "缺少必填纯文本正文"}
</span>
<span className="rounded-full border border-stone-200 bg-white/90 px-3 py-1 text-xs text-stone-600">
  {hasHtmlTemplate ? "已填写可选 HTML 正文" : "HTML 正文可选"}
</span>

{renderFieldLabel('默认模板主题', true)}
{renderFieldLabel('默认模板正文（纯文本）', true)}
{renderFieldLabel('默认模板正文（HTML，可保留格式）')}

<div className="text-xs leading-6 text-stone-500">
  导入只会自动带入正文内容，不会自动生成主题。HTML 正文可选；如果导入文件带样式，系统会自动生成纯文本正文供保存使用。
</div>
```

- [ ] **Step 5: Run frontend verification**

Run:

```bash
cd frontend
npm run lint
npm run build
```

Expected:

```text
eslint exits 0
vite build completes successfully
```

Manual smoke:

```text
1. 新建身份，先不填模板，点击“保存身份”，看到“请先填写默认套磁信主题和纯文本正文”。
2. 新建身份，不保存，直接导入 .docx/.html/.txt 模板文件，确认纯文本正文自动填入。
3. 导入后主题仍为空时，页面提示“请继续填写模板主题后再保存身份”。
4. 补齐主题后保存成功。
5. 在“模板润色”和“固定模板”之间切换，主题和正文内容保持不变。
```

- [ ] **Step 6: Commit the frontend implementation**

```bash
git add frontend/src/lib/api/identities.ts frontend/src/pages/ProfilePage.tsx
git commit -m "feat(frontend): require identity outreach template before save"
```

## Self-Review

- Spec coverage:
  - 身份保存必须有主题和纯文本正文：Task 1, Task 2, Task 3 覆盖。
  - 模板润色与固定模板共用同一套模板：Task 3 保持同一表单字段，不拆新状态。
  - 导入文件时自动生成纯文本正文：Task 1 服务契约测试、Task 2 后端保留解析逻辑、Task 3 前端导入提示覆盖。
  - 导入文件不自动生成主题：Task 1 API 测试与 Task 3 导入反馈文案覆盖。
  - 首次保存前可导入模板：Task 1 新接口测试、Task 2 新接口实现、Task 3 前端 API 调整覆盖。
  - 历史脏数据仍会被任务入口拦截：Task 1 旧回归测试改写、Task 2 batch/task runtime 校验覆盖。
- Placeholder scan:
  - 未保留任何空白描述或“参照上一任务”式描述。
  - 每个代码步骤都给了实际函数名、字段名、命令和预期结果。
- Type consistency:
  - 前端统一使用 `importIdentityTemplate(file)`。
  - 后端统一使用 `get_required_template_detail(subject_template, body_text_template)`。
  - 所有校验文案都围绕“默认套磁信主题/纯文本正文”这一套命名，不再混用“模板正文”“套磁信模板正文”的旧措辞。
