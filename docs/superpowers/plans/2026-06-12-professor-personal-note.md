# 导师个人备注实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为每位导师增加私人备注字段，在首页和导师管理页用轻量图标提示、悬浮查看完整备注、点击快速编辑，并支持 CSV / XLSX 导入导出。

**架构：** 后端把备注作为 `professors.personal_note` 纯文本字段保存，所有导师列表 DTO 带出该字段，并新增一个只更新备注的 PATCH 接口。前端用共享 `ProfessorNoteButton` 和 `ProfessorNoteDialog` 接入首页行、导师管理行和导师管理完整编辑表单，列表只在已有备注时显示图标。

**技术栈：** FastAPI、SQLAlchemy、Alembic、Pydantic、unittest、React 19、TypeScript、Vitest、Testing Library、lucide-react、Tailwind CSS。

---

## 实施前注意

当前工作区可能有与本功能无关的未提交改动。开始实现前先运行：

```bash
rtk git status --short
```

只提交本计划列出的文件变更。不要回滚或混入已有无关修改。

当前 Alembic head 已确认是：

```bash
cd backend && uv run alembic heads
# 预期输出包含：20260611matchmat (head)
```

## 文件结构

后端：

- 创建：`backend/alembic/versions/20260612_add_professor_personal_note.py`
  - 给 `professors` 表添加 / 移除 `personal_note`。
- 修改：`backend/app/models/professor.py`
  - 在 `Professor` ORM 模型增加 `personal_note` 字段。
- 修改：`backend/app/schemas/professor.py`
  - DTO、upsert payload、备注专用 payload/response。
- 修改：`backend/app/services/professor_management.py`
  - payload 归一化、导入模板、导入解析、导出行和公式防护覆盖备注。
- 修改：`backend/app/api/professors.py`
  - list/get/create/update/import/export 序列化和 `PATCH /api/professors/{id}/note`。
- 测试：`backend/test/test_professor_management.py`
  - 字段归一化、模板、导入导出、旧模板兼容。
- 测试：`backend/test/test_api_endpoints.py`
  - API 创建/列表/管理列表/备注专用接口/导入导出端到端。

前端：

- 修改：`frontend/src/types/index.ts`
  - DTO 和 payload 增加 `personal_note`，新增 `ProfessorNoteUpdateDTO`。
- 修改：`frontend/src/lib/api/professorsApi.ts`
  - 新增 `updateProfessorNote` API。
- 创建：`frontend/src/components/molecules/ProfessorNoteButton.tsx`
  - 备注图标按钮、hover/focus 完整备注浮层。
- 创建：`frontend/src/components/molecules/ProfessorNoteDialog.tsx`
  - 只编辑备注的小弹窗。
- 测试：`frontend/src/components/molecules/ProfessorNoteButton.test.tsx`
- 测试：`frontend/src/components/molecules/ProfessorNoteDialog.test.tsx`
- 修改：`frontend/src/components/molecules/DashboardProfessorRow.tsx`
  - 姓名和标签之间显示备注图标。
- 修改：`frontend/src/components/molecules/DashboardProfessorRow.test.tsx`
- 修改：`frontend/src/components/molecules/ManagementProfessorRow.tsx`
  - 姓名和标签之间显示备注图标。
- 修改：`frontend/src/components/molecules/ManagementProfessorRow.test.tsx`
- 修改：`frontend/src/pages/HomePage.tsx`
  - 首页接入备注弹窗和保存状态。
- 修改：`frontend/src/pages/ProfessorsPage.tsx`
  - 导师管理页接入备注弹窗、完整编辑表单和导入导出隐私提示。
- 测试：`frontend/test/ProfessorsPageLayout.test.tsx`
  - 导出提示、完整编辑备注字段、管理页备注弹窗。
- 测试：`frontend/test/HomePageProfessorNotes.test.tsx`
  - 首页备注图标点击编辑和清空消失。

---

### 任务 1：后端数据模型和 schema

**文件：**
- 创建：`backend/alembic/versions/20260612_add_professor_personal_note.py`
- 修改：`backend/app/models/professor.py`
- 修改：`backend/app/schemas/professor.py`
- 测试：`backend/test/test_professor_management.py`

- [ ] **步骤 1：编写失败的 payload 归一化测试**

在 `backend/test/test_professor_management.py` 的 `test_normalize_professor_payload_trims_name_and_lowercases_email` 中，把输入 payload 加上备注，并把期望结果加上 `personal_note`：

```python
payload = ProfessorUpsertPayload(
    name="  张明远  ",
    email="  ZHANG@EXAMPLE.EDU  ",
    title=" 教授 ",
    university=" 示例大学 ",
    school=" 人工智能学院 ",
    department=" 计算机科学系 ",
    research_direction=" 大语言模型 ",
    recent_papers=" Paper A | Paper B ",
    profile_url=" https://example.edu/zhang ",
    source_url=" https://example.edu/faculty ",
    personal_note="  6 月 20 日上午 Zoom 面试  ",
)
```

期望 dict 增加：

```python
"personal_note": "6 月 20 日上午 Zoom 面试",
```

再新增一个测试：

```python
def test_normalize_professor_payload_clears_blank_personal_note(self) -> None:
    payload = ProfessorUpsertPayload(
        name="张明远",
        email="zhang@example.edu",
        personal_note="   ",
    )

    self.assertIsNone(normalize_professor_payload(payload)["personal_note"])
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_professor_management.ProfessorManagementServiceTests.test_normalize_professor_payload_trims_name_and_lowercases_email backend.test.test_professor_management.ProfessorManagementServiceTests.test_normalize_professor_payload_clears_blank_personal_note
```

预期：FAIL，`ProfessorUpsertPayload` 不接受或 `normalize_professor_payload` 不返回 `personal_note`。

- [ ] **步骤 3：实现迁移、模型和 schema**

创建 `backend/alembic/versions/20260612_add_professor_personal_note.py`：

```python
"""add professor personal note

Revision ID: 20260612profnote
Revises: 20260611matchmat
Create Date: 2026-06-12 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260612profnote"
down_revision: Union[str, Sequence[str], None] = "20260611matchmat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("professors", sa.Column("personal_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("professors", "personal_note")
```

修改 `backend/app/models/professor.py`，在 `skip_reason` 后、`archived_at` 前增加：

```python
personal_note: Mapped[str | None] = mapped_column(Text, nullable=True)
```

修改 `backend/app/schemas/professor.py`：

```python
MAX_PERSONAL_NOTE_LENGTH = 10_000
```

给 `ProfessorRead`、`ProfessorDashboardItemRead`、`ProfessorManagementItemRead` 增加：

```python
personal_note: str | None
```

给 `ProfessorUpsertPayload` 增加：

```python
personal_note: str | None = Field(default=None, max_length=MAX_PERSONAL_NOTE_LENGTH)
```

把 `"personal_note"` 加入 `_strip_string_fields` 的字段列表。

新增备注专用 schema：

```python
class ProfessorNoteUpdatePayload(BaseModel):
    personal_note: str | None = Field(default=None, max_length=MAX_PERSONAL_NOTE_LENGTH)

    @field_validator("personal_note", mode="before")
    @classmethod
    def _strip_personal_note(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class ProfessorNoteUpdateRead(ApiSchema):
    id: int
    personal_note: str | None
```

修改 `backend/app/services/professor_management.py` 的 `normalize_professor_payload` 返回值，增加：

```python
"personal_note": payload.personal_note,
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_professor_management.ProfessorManagementServiceTests.test_normalize_professor_payload_trims_name_and_lowercases_email backend.test.test_professor_management.ProfessorManagementServiceTests.test_normalize_professor_payload_clears_blank_personal_note
```

预期：PASS。

- [ ] **步骤 5：提交**

```bash
rtk git add backend/alembic/versions/20260612_add_professor_personal_note.py backend/app/models/professor.py backend/app/schemas/professor.py backend/app/services/professor_management.py backend/test/test_professor_management.py
rtk git commit -m "feat(backend): add professor personal note field"
```

---

### 任务 2：后端导入导出支持 personal_note

**文件：**
- 修改：`backend/app/services/professor_management.py`
- 修改：`backend/test/test_professor_management.py`

- [ ] **步骤 1：编写失败的导入导出测试**

在 `backend/test/test_professor_management.py` 增加 / 修改以下断言。

在 `test_build_professor_template_supports_csv_and_xlsx_and_rejects_unknown_format` 中加入：

```python
self.assertIn("personal_note", csv_content.decode("utf-8-sig").splitlines()[-2])
self.assertIn("personal_note", rows[-2])
```

新增测试：

```python
def test_parse_csv_import_reads_personal_note_and_legacy_template_omits_marker(self) -> None:
    csv_content = (
        ",".join(PROFESSOR_TEMPLATE_COLUMNS)
        + "\n"
        + "张三,zhang@example.edu,教授,示例大学,人工智能学院,计算机科学系,大语言模型,,,,高意愿,  6 月 20 日 Zoom 面试  \n"
    ).encode("utf-8-sig")

    parsed = parse_professor_import_file("professors.csv", csv_content)

    self.assertEqual(parsed.failed_count, 0)
    self.assertEqual(parsed.data["zhang@example.edu"]["personal_note"], "6 月 20 日 Zoom 面试")
    self.assertTrue(parsed.data["zhang@example.edu"]["has_personal_note_column"])

    legacy_columns = [column for column in PROFESSOR_TEMPLATE_COLUMNS if column not in {"tags", "personal_note"}]
    legacy_content = (
        ",".join(legacy_columns)
        + "\n"
        + "李四,li@example.edu,教授,示例大学,人工智能学院,计算机科学系,大语言模型,,,,\n"
    ).encode("utf-8-sig")

    legacy = parse_professor_import_file("professors.csv", legacy_content)

    self.assertIsNone(legacy.data["li@example.edu"]["personal_note"])
    self.assertFalse(legacy.data["li@example.edu"]["has_personal_note_column"])
```

修改 `test_build_professor_export_csv_can_be_imported_without_changes` 的 professor：

```python
personal_note="已约 6 月 20 日面试",
```

并加入：

```python
self.assertIn("已约 6 月 20 日面试", decoded)
self.assertEqual(parsed.data["li@example.edu"]["personal_note"], "已约 6 月 20 日面试")
```

修改 `test_build_professor_export_escapes_spreadsheet_formulas` 的 professor：

```python
personal_note="=private note",
```

并加入：

```python
self.assertEqual(csv_rows[1][11], "'=private note")
self.assertEqual(rows[1][11], "'=private note")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_professor_management.ProfessorManagementServiceTests.test_build_professor_template_supports_csv_and_xlsx_and_rejects_unknown_format backend.test.test_professor_management.ProfessorManagementServiceTests.test_parse_csv_import_reads_personal_note_and_legacy_template_omits_marker backend.test.test_professor_management.ProfessorManagementServiceTests.test_build_professor_export_csv_can_be_imported_without_changes backend.test.test_professor_management.ProfessorManagementServiceTests.test_build_professor_export_escapes_spreadsheet_formulas
```

预期：FAIL，模板、解析和导出尚未处理 `personal_note`。

- [ ] **步骤 3：实现导入导出**

修改 `backend/app/services/professor_management.py`：

`PROFESSOR_TEMPLATE_COLUMNS` 在 `"tags"` 后增加：

```python
"personal_note",
```

把 legacy 列改成只移除新增的可选列：

```python
PROFESSOR_LEGACY_TEMPLATE_COLUMNS = [
    column
    for column in PROFESSOR_TEMPLATE_COLUMNS
    if column not in {"tags", "personal_note"}
]
```

`PROFESSOR_TEMPLATE_HELP_LINES` 增加：

```python
"# personal_note：个人备注，仅供自己记录；导出文件会包含该字段，请谨慎分享。",
```

`PROFESSOR_TEMPLATE_EXAMPLE_ROW` 末尾增加：

```python
"6 月 20 日上午 Zoom 面试；后续补发成绩单",
```

在 `_normalize_import_row` 中保留列是否存在：

```python
has_personal_note_column = "personal_note" in row
```

返回 dict 增加：

```python
"personal_note": raw_values["personal_note"] or None,
"has_personal_note_column": has_personal_note_column,
```

修改 `_professor_to_export_row`，在 tags 后追加：

```python
_export_cell(getattr(professor, "personal_note", None)),
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_professor_management
```

预期：PASS。

- [ ] **步骤 5：提交**

```bash
rtk git add backend/app/services/professor_management.py backend/test/test_professor_management.py
rtk git commit -m "feat(backend): support professor note import export"
```

---

### 任务 3：后端 API 创建、列表、快速备注更新

**文件：**
- 修改：`backend/app/api/professors.py`
- 修改：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写失败的 API 测试**

在 `backend/test/test_api_endpoints.py` 的 `ApiEndpointTests` 中，靠近教授管理相关测试处新增：

```python
def test_professor_personal_note_create_list_update_and_clear(self) -> None:
    create_response = self.client.post(
        "/api/professors",
        json={
            "name": "备注导师",
            "email": "note-professor@example.edu",
            "title": "Professor",
            "university": "Note University",
            "school": "School",
            "department": "Dept",
            "research_direction": "AI",
            "recent_papers": [],
            "profile_url": None,
            "source_url": None,
            "personal_note": "  初次沟通很积极  ",
            "tag_ids": [],
        },
    )
    self.assertEqual(create_response.status_code, 201, msg=create_response.text)
    professor_id = create_response.json()["id"]
    self.assertEqual(create_response.json()["personal_note"], "初次沟通很积极")

    dashboard = self.client.get("/api/professors")
    self.assertEqual(dashboard.status_code, 200, msg=dashboard.text)
    dashboard_professor = next(item for item in dashboard.json() if item["id"] == professor_id)
    self.assertEqual(dashboard_professor["personal_note"], "初次沟通很积极")

    management = self.client.get("/api/professors/management", params={"archived": "active"})
    self.assertEqual(management.status_code, 200, msg=management.text)
    management_professor = next(item for item in management.json() if item["id"] == professor_id)
    self.assertEqual(management_professor["personal_note"], "初次沟通很积极")

    update_note = self.client.patch(
        f"/api/professors/{professor_id}/note",
        json={"personal_note": " 6 月 20 日 Zoom 面试 "},
    )
    self.assertEqual(update_note.status_code, 200, msg=update_note.text)
    self.assertEqual(
        update_note.json(),
        {"id": professor_id, "personal_note": "6 月 20 日 Zoom 面试"},
    )

    cleared = self.client.patch(
        f"/api/professors/{professor_id}/note",
        json={"personal_note": "   "},
    )
    self.assertEqual(cleared.status_code, 200, msg=cleared.text)
    self.assertEqual(cleared.json(), {"id": professor_id, "personal_note": None})
```

再新增不存在导师测试：

```python
def test_update_professor_personal_note_returns_404_for_missing_professor(self) -> None:
    response = self.client.patch(
        "/api/professors/999999/note",
        json={"personal_note": "不存在"},
    )

    self.assertEqual(response.status_code, 404)
    self.assertEqual(response.json()["detail"], "未找到导师")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_api_endpoints.ApiEndpointTests.test_professor_personal_note_create_list_update_and_clear backend.test.test_api_endpoints.ApiEndpointTests.test_update_professor_personal_note_returns_404_for_missing_professor
```

预期：FAIL，响应缺少 `personal_note` 或 `/note` 路由不存在。

- [ ] **步骤 3：实现 API**

修改 `backend/app/api/professors.py` imports：

```python
ProfessorNoteUpdatePayload,
ProfessorNoteUpdateRead,
```

在 `list_professors` 的 `ProfessorDashboardItemRead(...)` 增加：

```python
personal_note=professor.personal_note,
```

在 `get_professor` 的 `ProfessorRead(...)` 增加：

```python
personal_note=professor.personal_note,
```

在 `update_professor` 字段赋值里增加：

```python
professor.personal_note = professor_data["personal_note"]
```

在 `_serialize_management_professor` 增加：

```python
personal_note=professor.personal_note,
```

新增路由，放在完整 `update_professor` 后、archive 前：

```python
@router.patch("/{professor_id}/note", response_model=ProfessorNoteUpdateRead)
async def update_professor_note(
    professor_id: int,
    payload: ProfessorNoteUpdatePayload,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorNoteUpdateRead:
    professor = await session.get(Professor, professor_id)
    if not professor:
        raise HTTPException(status_code=404, detail="未找到导师")

    professor.personal_note = payload.personal_note
    professor.updated_at = utc_now()
    await _record_professor_log(
        session,
        professor,
        "professor.personal_note_updated",
        metadata={
            "has_personal_note": bool(payload.personal_note),
            "personal_note_length": len(payload.personal_note or ""),
        },
    )
    await session.commit()

    return ProfessorNoteUpdateRead(
        id=professor.id,
        personal_note=professor.personal_note,
    )
```

修改 `import_professors_from_file`：

创建新导师时过滤掉导入元标记：

```python
professor_data = {
    key: value
    for key, value in payload.items()
    if key not in {"tag_names", "has_personal_note_column"}
}
```

更新已有导师时，在 `source_url` 后增加：

```python
if payload.get("has_personal_note_column"):
    professor.personal_note = payload["personal_note"]
```

修改 `record_operation_log` metadata，不记录正文，只增加聚合信息：

```python
"personal_note_column_present": any(
    bool(payload.get("has_personal_note_column"))
    for payload in parsed.data.values()
),
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_api_endpoints.ApiEndpointTests.test_professor_personal_note_create_list_update_and_clear backend.test.test_api_endpoints.ApiEndpointTests.test_update_professor_personal_note_returns_404_for_missing_professor
```

预期：PASS。

- [ ] **步骤 5：提交**

```bash
rtk git add backend/app/api/professors.py backend/test/test_api_endpoints.py
rtk git commit -m "feat(api): add professor personal note endpoint"
```

---

### 任务 4：后端导入导出端到端覆盖

**文件：**
- 修改：`backend/test/test_api_endpoints.py`
- 修改：`backend/app/api/professors.py`
- 修改：`backend/app/services/professor_management.py`

- [ ] **步骤 1：编写失败的端到端测试**

扩展 `test_professor_template_download_and_import_file_upserts_existing_records`：

模板断言加上：

```python
self.assertIn("# personal_note：个人备注", csv_template.text)
self.assertIn("tags,personal_note", csv_template.text)
```

创建 `李教授` 时加：

```python
"personal_note": "原备注保留",
```

旧模板 CSV 导入后，在 `li_professor` 断言中加：

```python
self.assertEqual(li_professor["personal_note"], "原备注保留")
```

再在同一测试中用新模板导入覆盖备注：

```python
new_template_content = (
    "name,email,title,university,school,department,research_direction,recent_papers,profile_url,source_url,tags,personal_note\n"
    "李教授,li@example.edu,副教授,New University,School of AI,AI,Updated direction,Paper 1,https://example.edu/li,https://example.edu/faculty,高意愿,新备注\n"
).encode("utf-8-sig")
new_template_import = self.client.post(
    "/api/professors/import-file",
    files={"file": ("professors.csv", io.BytesIO(new_template_content), "text/csv")},
)
self.assertEqual(new_template_import.status_code, 200, msg=new_template_import.text)
refreshed_after_note = self.client.get("/api/professors/management", params={"archived": "active"}).json()
li_after_note = next(item for item in refreshed_after_note if item["email"] == "li@example.edu")
self.assertEqual(li_after_note["personal_note"], "新备注")
```

扩展 `test_professor_export_file_can_be_reimported` 或相邻导出测试，把创建 professor payload 加：

```python
"personal_note": "导出备注",
```

并断言 CSV / XLSX 导出内容包含 `personal_note` 和 `导出备注`，重新导入后管理列表仍有备注。

- [ ] **步骤 2：运行端到端测试验证当前覆盖**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_api_endpoints.ApiEndpointTests.test_professor_template_download_and_import_file_upserts_existing_records
```

预期：PASS。这个任务补的是端到端回归覆盖；失败时按步骤 3 的确定修正点补齐实现。

- [ ] **步骤 3：确认端到端实现点**

检查并确保以下实现点存在：

```python
if key not in {"tag_names", "has_personal_note_column"}
```

```python
if all(column in normalized for column in PROFESSOR_LEGACY_TEMPLATE_COLUMNS):
    return
```

确认 `backend/app/api/professors.py` 中旧模板导入不覆盖已有备注：

```python
if payload.get("has_personal_note_column"):
    professor.personal_note = payload["personal_note"]
```

- [ ] **步骤 4：运行后端相关测试**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_professor_management backend.test.test_api_endpoints.ApiEndpointTests.test_professor_personal_note_create_list_update_and_clear backend.test.test_api_endpoints.ApiEndpointTests.test_update_professor_personal_note_returns_404_for_missing_professor backend.test.test_api_endpoints.ApiEndpointTests.test_professor_template_download_and_import_file_upserts_existing_records
```

预期：PASS。

- [ ] **步骤 5：提交**

```bash
rtk git add backend/test/test_api_endpoints.py backend/app/api/professors.py backend/app/services/professor_management.py
rtk git commit -m "test(api): cover professor note import export"
```

---

### 任务 5：前端类型和 API client

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/lib/api/professorsApi.ts`

- [ ] **步骤 1：编写失败的类型使用测试**

不新增单独类型测试；本任务用 TypeScript 编译作为失败信号。先在 `frontend/src/lib/api/professorsApi.ts` 增加一个还不存在类型的函数调用点：

```typescript
export const updateProfessorNote = (professorId: number, personalNote: string | null) =>
  apiFetch<ProfessorNoteUpdateDTO>(`/api/professors/${professorId}/note`, {
    method: 'PATCH',
    body: JSON.stringify({ personal_note: personalNote }),
  });
```

- [ ] **步骤 2：运行类型检查验证失败**

运行：

```bash
cd frontend && npm run build
```

预期：FAIL，`ProfessorNoteUpdateDTO` 未定义或未导入。

- [ ] **步骤 3：补全类型和 API**

修改 `frontend/src/types/index.ts`：

给 `ProfessorDashboardItemDTO`、`ProfessorDTO`、`ProfessorManagementItemDTO` 增加：

```typescript
personal_note: string | null;
```

给 `ProfessorUpsertPayloadDTO` 增加：

```typescript
personal_note: string | null;
```

新增：

```typescript
export interface ProfessorNoteUpdateDTO {
  id: number;
  personal_note: string | null;
}
```

修改 `frontend/src/lib/api/professorsApi.ts` import type 增加：

```typescript
ProfessorNoteUpdateDTO,
```

保留步骤 1 的 `updateProfessorNote`。

- [ ] **步骤 4：运行类型检查验证通过**

运行：

```bash
cd frontend && npm run build
```

预期：PASS。

- [ ] **步骤 5：提交**

```bash
rtk git add frontend/src/types/index.ts frontend/src/lib/api/professorsApi.ts
rtk git commit -m "feat(frontend): add professor note api types"
```

---

### 任务 6：共享备注按钮组件

**文件：**
- 创建：`frontend/src/components/molecules/ProfessorNoteButton.tsx`
- 创建：`frontend/src/components/molecules/ProfessorNoteButton.test.tsx`

- [ ] **步骤 1：编写失败的组件测试**

创建 `frontend/src/components/molecules/ProfessorNoteButton.test.tsx`：

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProfessorNoteButton } from "./ProfessorNoteButton";

describe("ProfessorNoteButton", () => {
  it("renders nothing when note is empty after trimming", () => {
    const { container, rerender } = render(
      <ProfessorNoteButton professorName="张明远" personalNote={null} onEdit={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();

    rerender(
      <ProfessorNoteButton professorName="张明远" personalNote="   " onEdit={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows full note on hover and focus and calls edit on click", () => {
    const handleEdit = vi.fn();
    render(
      <ProfessorNoteButton
        professorName="张明远"
        personalNote={"6 月 20 日 Zoom 面试\n需要补发成绩单"}
        onEdit={handleEdit}
      />,
    );

    const button = screen.getByRole("button", { name: "编辑张明远的个人备注" });
    fireEvent.mouseEnter(button);
    expect(screen.getByRole("dialog", { name: "张明远的个人备注" })).toHaveTextContent(
      "6 月 20 日 Zoom 面试",
    );
    expect(screen.getByRole("dialog", { name: "张明远的个人备注" })).toHaveTextContent(
      "需要补发成绩单",
    );

    fireEvent.mouseLeave(button);
    expect(screen.queryByRole("dialog", { name: "张明远的个人备注" })).not.toBeInTheDocument();

    fireEvent.focus(button);
    expect(screen.getByRole("dialog", { name: "张明远的个人备注" })).toBeInTheDocument();

    fireEvent.click(button);
    expect(handleEdit).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend && npm run test:dom -- ProfessorNoteButton.test.tsx
```

预期：FAIL，组件不存在。

- [ ] **步骤 3：实现组件**

创建 `frontend/src/components/molecules/ProfessorNoteButton.tsx`：

```tsx
import { useState } from "react";
import { StickyNote } from "lucide-react";

type ProfessorNoteButtonProps = {
  professorName: string;
  personalNote: string | null | undefined;
  onEdit: () => void;
};

export const ProfessorNoteButton = ({
  professorName,
  personalNote,
  onEdit,
}: ProfessorNoteButtonProps) => {
  const note = personalNote?.trim();
  const [open, setOpen] = useState(false);

  if (!note) {
    return null;
  }

  return (
    <span
      className="relative inline-flex"
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-label={`编辑${professorName}的个人备注`}
        onClick={(event) => {
          event.stopPropagation();
          onEdit();
        }}
        onMouseEnter={() => setOpen(true)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-amber-200 bg-amber-50 text-amber-700 transition hover:border-amber-300 hover:bg-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300/40"
      >
        <StickyNote className="h-3.5 w-3.5" />
      </button>
      {open ? (
        <span
          role="dialog"
          aria-label={`${professorName}的个人备注`}
          className="absolute left-0 top-[calc(100%+0.35rem)] z-50 max-h-60 w-[min(22.5rem,80vw)] overflow-y-auto whitespace-pre-wrap rounded-2xl border border-stone-200 bg-white px-3 py-2 text-left text-sm leading-6 text-stone-700 shadow-[0_18px_42px_-24px_rgba(41,37,36,0.45)]"
        >
          {note}
        </span>
      ) : null}
    </span>
  );
};
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd frontend && npm run test:dom -- ProfessorNoteButton.test.tsx
```

预期：PASS。

- [ ] **步骤 5：提交**

```bash
rtk git add frontend/src/components/molecules/ProfessorNoteButton.tsx frontend/src/components/molecules/ProfessorNoteButton.test.tsx
rtk git commit -m "feat(frontend): add professor note button"
```

---

### 任务 7：共享备注编辑弹窗

**文件：**
- 创建：`frontend/src/components/molecules/ProfessorNoteDialog.tsx`
- 创建：`frontend/src/components/molecules/ProfessorNoteDialog.test.tsx`

- [ ] **步骤 1：编写失败的组件测试**

创建 `frontend/src/components/molecules/ProfessorNoteDialog.test.tsx`：

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProfessorNoteDialog } from "./ProfessorNoteDialog";

describe("ProfessorNoteDialog", () => {
  it("renders note context and saves edited value", () => {
    const handleSave = vi.fn();
    render(
      <ProfessorNoteDialog
        open
        professor={{ id: 1, name: "张明远", university: "示例大学", school: "计算机学院" }}
        initialNote="旧备注"
        saving={false}
        onSave={handleSave}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole("dialog", { name: "编辑个人备注" })).toBeInTheDocument();
    expect(screen.getByText("张明远")).toBeInTheDocument();
    expect(screen.getByText("示例大学 / 计算机学院")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: "个人备注" }), {
      target: { value: "新备注" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存备注" }));

    expect(handleSave).toHaveBeenCalledWith("新备注");
  });

  it("does not render when closed and disables save while saving", () => {
    const { rerender } = render(
      <ProfessorNoteDialog
        open={false}
        professor={null}
        initialNote=""
        saving={false}
        onSave={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByRole("dialog", { name: "编辑个人备注" })).not.toBeInTheDocument();

    rerender(
      <ProfessorNoteDialog
        open
        professor={{ id: 1, name: "张明远", university: null, school: null }}
        initialNote=""
        saving
        onSave={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "保存备注" })).toBeDisabled();
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend && npm run test:dom -- ProfessorNoteDialog.test.tsx
```

预期：FAIL，组件不存在。

- [ ] **步骤 3：实现组件**

创建 `frontend/src/components/molecules/ProfessorNoteDialog.tsx`：

```tsx
import { useEffect, useState } from "react";
import { Loader2, X } from "lucide-react";
import { useDismissableLayerClick } from "@/lib/useDismissableLayerClick";

type ProfessorNoteDialogProfessor = {
  id: number;
  name: string;
  university?: string | null;
  school?: string | null;
};

type ProfessorNoteDialogProps = {
  open: boolean;
  professor: ProfessorNoteDialogProfessor | null;
  initialNote: string | null | undefined;
  saving: boolean;
  onSave: (note: string) => void;
  onClose: () => void;
};

export const ProfessorNoteDialog = ({
  open,
  professor,
  initialNote,
  saving,
  onSave,
  onClose,
}: ProfessorNoteDialogProps) => {
  const [draft, setDraft] = useState(initialNote ?? "");
  const {
    onBackdropClick,
    onBackdropMouseDown,
    onContentClick,
    onContentMouseDown,
  } = useDismissableLayerClick(onClose);

  useEffect(() => {
    if (open) {
      setDraft(initialNote ?? "");
    }
  }, [initialNote, open]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);

  if (!open || !professor) {
    return null;
  }

  const affiliation = [professor.university, professor.school].filter(Boolean).join(" / ");

  return (
    <div
      role="dialog"
      aria-label="编辑个人备注"
      aria-modal="true"
      className="fixed inset-0 z-[90] flex items-center justify-center bg-stone-950/35 p-4 backdrop-blur-md"
      onClick={onBackdropClick}
      onMouseDown={onBackdropMouseDown}
    >
      <div
        className="w-full max-w-xl rounded-[28px] border border-stone-200 bg-white p-6 shadow-[0_34px_90px_-32px_rgba(41,37,36,0.5)]"
        onClick={onContentClick}
        onMouseDown={onContentMouseDown}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold text-stone-900">编辑个人备注</h2>
            <div className="mt-2 text-sm font-medium text-stone-800">{professor.name}</div>
            {affiliation ? (
              <div className="mt-1 text-sm text-stone-500">{affiliation}</div>
            ) : null}
          </div>
          <button
            type="button"
            aria-label="关闭备注编辑"
            onClick={onClose}
            disabled={saving}
            className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-stone-200 text-stone-500 transition hover:border-stone-300 hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <label className="mt-5 block">
          <div className="mb-2 text-sm font-medium text-stone-800">个人备注</div>
          <textarea
            aria-label="个人备注"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            className="min-h-36 w-full rounded-2xl border border-stone-200 bg-white px-3 py-3 text-sm leading-6 text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15"
            placeholder="例如：6 月 20 日上午 Zoom 面试；导师回复很积极；后续需要补发成绩单。"
          />
        </label>

        <div className="mt-5 flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => onSave(draft)}
            disabled={saving}
            className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            保存备注
          </button>
        </div>
      </div>
    </div>
  );
};
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd frontend && npm run test:dom -- ProfessorNoteDialog.test.tsx
```

预期：PASS。

- [ ] **步骤 5：提交**

```bash
rtk git add frontend/src/components/molecules/ProfessorNoteDialog.tsx frontend/src/components/molecules/ProfessorNoteDialog.test.tsx
rtk git commit -m "feat(frontend): add professor note dialog"
```

---

### 任务 8：首页和导师管理行显示备注图标

**文件：**
- 修改：`frontend/src/components/molecules/DashboardProfessorRow.tsx`
- 修改：`frontend/src/components/molecules/DashboardProfessorRow.test.tsx`
- 修改：`frontend/src/components/molecules/ManagementProfessorRow.tsx`
- 修改：`frontend/src/components/molecules/ManagementProfessorRow.test.tsx`
- 修改：`frontend/test/DashboardProfessorRow.test.tsx`

- [ ] **步骤 1：编写失败的行组件测试**

在 `frontend/src/components/molecules/DashboardProfessorRow.test.tsx` 的 `professor` fixture 加：

```typescript
personal_note: null,
```

新增测试：

```tsx
it("shows note icon between name and tags when professor has a note", () => {
  const handleEditNote = vi.fn();
  render(
    <DashboardProfessorRow
      professor={{ ...professor, personal_note: "6 月 20 日 Zoom 面试" }}
      selected={false}
      bulkDisabled={false}
      scoring={false}
      canCalculateMatch
      statusLabel="未发送"
      timeHighlight={null}
      timeLabel={null}
      onToggleSelection={vi.fn()}
      onCalculateMatch={vi.fn()}
      onOpenWorkspace={vi.fn()}
      onEditNote={handleEditNote}
    />,
  );

  const nameLine = screen.getByTestId("dashboard-professor-name-line");
  fireEvent.mouseEnter(within(nameLine).getByRole("button", { name: "编辑张明远的个人备注" }));
  expect(screen.getByRole("dialog", { name: "张明远的个人备注" })).toHaveTextContent("6 月 20 日 Zoom 面试");
  fireEvent.click(within(nameLine).getByRole("button", { name: "编辑张明远的个人备注" }));
  expect(handleEditNote).toHaveBeenCalledTimes(1);
});
```

在 `frontend/src/components/molecules/ManagementProfessorRow.test.tsx` 的 fixture 加：

```typescript
personal_note: null,
```

新增类似测试：

```tsx
it("shows note icon in the name line when professor has a note", () => {
  const handleEditNote = vi.fn();
  render(
    <ManagementProfessorRow
      professor={{ ...professor, personal_note: "需要补发成绩单" }}
      checked={false}
      selectable
      tableColumns="lg:grid-cols-8"
      onToggleSelection={vi.fn()}
      onEdit={vi.fn()}
      onArchive={vi.fn()}
      onRestore={vi.fn()}
      onEditNote={handleEditNote}
    />,
  );

  const nameLine = screen.getByTestId("management-professor-name-line");
  expect(within(nameLine).getByRole("button", { name: "编辑李伟的个人备注" })).toBeInTheDocument();
  fireEvent.click(within(nameLine).getByRole("button", { name: "编辑李伟的个人备注" }));
  expect(handleEditNote).toHaveBeenCalledTimes(1);
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend && npm run test:dom -- DashboardProfessorRow.test.tsx ManagementProfessorRow.test.tsx
```

预期：FAIL，行组件不接受 `onEditNote` 或不显示备注图标。

- [ ] **步骤 3：实现行组件接入**

修改 `DashboardProfessorRow.tsx`：

```tsx
import { ProfessorNoteButton } from "@/components/molecules/ProfessorNoteButton";
```

props 增加：

```typescript
onEditNote?: () => void;
```

参数解构增加 `onEditNote`。

在姓名 `<div>` 和 `ProfessorTagChips` 之间插入：

```tsx
<ProfessorNoteButton
  professorName={professor.name}
  personalNote={professor.personal_note}
  onEdit={onEditNote ?? (() => undefined)}
/>
```

修改 `ManagementProfessorRow.tsx` 同理增加 import、prop、解构，并在姓名和标签之间插入 `ProfessorNoteButton`。

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd frontend && npm run test:dom -- DashboardProfessorRow.test.tsx ManagementProfessorRow.test.tsx
```

预期：PASS。

- [ ] **步骤 5：提交**

```bash
rtk git add frontend/src/components/molecules/DashboardProfessorRow.tsx frontend/src/components/molecules/DashboardProfessorRow.test.tsx frontend/src/components/molecules/ManagementProfessorRow.tsx frontend/src/components/molecules/ManagementProfessorRow.test.tsx frontend/test/DashboardProfessorRow.test.tsx
rtk git commit -m "feat(frontend): show professor note indicators"
```

---

### 任务 9：首页备注快速编辑

**文件：**
- 修改：`frontend/src/pages/HomePage.tsx`
- 测试：新建 `frontend/test/HomePageProfessorNotes.test.tsx` 或修改 `frontend/test/HomePageOnboarding.test.tsx`

- [ ] **步骤 1：编写失败的首页集成测试**

创建 `frontend/test/HomePageProfessorNotes.test.tsx`，按 `HomePageOnboarding.test.tsx` 的 mock 结构 mock `@/lib/api/professorsApi`，至少包含：

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HomePage } from "@/pages/HomePage";
import { NotificationProvider } from "@/context/NotificationContext";
import { SelectionProvider } from "@/context/SelectionContext";
import type { ProfessorDashboardItemDTO } from "@/types";

const listProfessors = vi.fn();
const updateProfessorNote = vi.fn();
const listProfessorTags = vi.fn();

vi.mock("@/lib/api/professorsApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/professorsApi")>(
    "@/lib/api/professorsApi",
  );
  return {
    ...actual,
    listProfessors,
    listProfessorTags,
    updateProfessorNote,
  };
});
```

使用现有选择上下文如测试需要可 mock `useSelectionContext`，让 `selectedIdentityId` 为 `1`。测试主体：

```tsx
it("edits and clears an existing professor note from homepage row", async () => {
  const professor: ProfessorDashboardItemDTO = {
    id: 101,
    name: "张明远",
    email: "zhang@example.edu",
    title: "教授",
    university: "示例大学",
    school: "计算机学院",
    department: "人工智能系",
    research_direction: "大语言模型",
    recent_papers: [],
    match_score: null,
    sent_count: 0,
    status: "not_contacted",
    last_sent_at: null,
    last_replied_at: null,
    personal_note: "旧备注",
    tags: [],
  };
  listProfessors.mockResolvedValue([professor]);
  listProfessorTags.mockResolvedValue([]);
  updateProfessorNote.mockResolvedValue({ id: 101, personal_note: null });

  render(
    <MemoryRouter>
      <NotificationProvider>
        <SelectionProvider>
          <HomePage />
        </SelectionProvider>
      </NotificationProvider>
    </MemoryRouter>,
  );

  const noteButton = await screen.findByRole("button", { name: "编辑张明远的个人备注" });
  fireEvent.click(noteButton);
  fireEvent.change(screen.getByRole("textbox", { name: "个人备注" }), {
    target: { value: "" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存备注" }));

  await waitFor(() => expect(updateProfessorNote).toHaveBeenCalledWith(101, ""));
  await waitFor(() => {
    expect(screen.queryByRole("button", { name: "编辑张明远的个人备注" })).not.toBeInTheDocument();
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend && npm run test:dom -- HomePageProfessorNotes.test.tsx
```

预期：FAIL，首页尚未接入备注弹窗。

- [ ] **步骤 3：实现首页接入**

修改 `frontend/src/pages/HomePage.tsx`：

import 增加：

```tsx
import { ProfessorNoteDialog } from "@/components/molecules/ProfessorNoteDialog";
import { updateProfessorNote } from "@/lib/api/professorsApi";
```

在 state 区增加：

```tsx
const [noteEditorProfessor, setNoteEditorProfessor] =
  useState<ProfessorDashboardItemDTO | null>(null);
const [savingProfessorNote, setSavingProfessorNote] = useState(false);
```

增加保存函数：

```tsx
const saveProfessorNote = async (note: string) => {
  if (!noteEditorProfessor) {
    return;
  }
  setSavingProfessorNote(true);
  try {
    const updated = await updateProfessorNote(noteEditorProfessor.id, note);
    setProfessors((previous) =>
      previous.map((professor) =>
        professor.id === updated.id
          ? { ...professor, personal_note: updated.personal_note }
          : professor,
      ),
    );
    setNoteEditorProfessor(null);
    notifySuccess("备注已更新", `已更新“${noteEditorProfessor.name}”的个人备注。`);
  } catch (saveError) {
    const message = saveError instanceof Error ? saveError.message : "保存备注失败";
    notifyError("保存备注失败", message);
  } finally {
    setSavingProfessorNote(false);
  }
};
```

给 `DashboardProfessorRow` 增加：

```tsx
onEditNote={() => setNoteEditorProfessor(professor)}
```

在 tag dialogs 附近渲染：

```tsx
<ProfessorNoteDialog
  open={Boolean(noteEditorProfessor)}
  professor={noteEditorProfessor}
  initialNote={noteEditorProfessor?.personal_note ?? ""}
  saving={savingProfessorNote}
  onSave={(note) => void saveProfessorNote(note)}
  onClose={() => {
    if (!savingProfessorNote) {
      setNoteEditorProfessor(null);
    }
  }}
/>
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd frontend && npm run test:dom -- HomePageProfessorNotes.test.tsx
```

预期：PASS。

- [ ] **步骤 5：提交**

```bash
rtk git add frontend/src/pages/HomePage.tsx frontend/test/HomePageProfessorNotes.test.tsx
rtk git commit -m "feat(frontend): edit professor notes from home"
```

---

### 任务 10：导师管理页备注编辑和完整表单

**文件：**
- 修改：`frontend/src/pages/ProfessorsPage.tsx`
- 修改：`frontend/test/ProfessorsPageLayout.test.tsx`

- [ ] **步骤 1：编写失败的导师管理测试**

在 `frontend/test/ProfessorsPageLayout.test.tsx` 的 mock import 中加入 `updateProfessorNote`。顶部 mock 函数增加：

```typescript
const updateProfessorNote = vi.fn();
```

`vi.mock("@/lib/api/professorsApi"` 返回对象增加：

```typescript
updateProfessorNote,
```

`professor` fixture 增加：

```typescript
personal_note: "已有备注",
```

`buildProfessor` 返回值增加：

```typescript
personal_note: null,
```

新增测试：

```tsx
it("edits professor note from management row and hides icon after clearing", async () => {
  listProfessorsForManagement.mockResolvedValue([professor]);
  updateProfessorNote.mockResolvedValue({ id: professor.id, personal_note: null });
  renderPage();

  const noteButton = await screen.findByRole("button", { name: "编辑李教授的个人备注" });
  fireEvent.click(noteButton);
  expect(screen.getByRole("dialog", { name: "编辑个人备注" })).toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "个人备注" }), {
    target: { value: "" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存备注" }));

  await waitFor(() => expect(updateProfessorNote).toHaveBeenCalledWith(professor.id, ""));
  await waitFor(() => {
    expect(screen.queryByRole("button", { name: "编辑李教授的个人备注" })).not.toBeInTheDocument();
  });
});
```

新增完整表单测试：

```tsx
it("includes personal note in the full professor edit form payload", async () => {
  listProfessorsForManagement.mockResolvedValue([professor]);
  updateProfessor.mockResolvedValue({ ...professor, personal_note: "更新后的备注" });
  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "编辑" }));
  expect(screen.getByRole("textbox", { name: "个人备注" })).toHaveValue("已有备注");

  fireEvent.change(screen.getByRole("textbox", { name: "个人备注" }), {
    target: { value: "更新后的备注" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存导师" }));

  await waitFor(() => {
    expect(updateProfessor).toHaveBeenCalledWith(
      professor.id,
      expect.objectContaining({ personal_note: "更新后的备注" }),
    );
  });
});
```

在导出弹窗测试中加：

```tsx
expect(screen.getByText("导出文件包含个人备注，请谨慎分享。")).toBeInTheDocument();
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend && npm run test:dom -- ProfessorsPageLayout.test.tsx
```

预期：FAIL，管理页尚未接入备注 API / 表单 / 导出提示。

- [ ] **步骤 3：实现导师管理页接入**

修改 `frontend/src/pages/ProfessorsPage.tsx`：

imports 增加：

```tsx
import { ProfessorNoteDialog } from "@/components/molecules/ProfessorNoteDialog";
import { updateProfessorNote } from "@/lib/api/professorsApi";
```

`ProfessorFormState` 增加：

```typescript
personal_note: string;
```

`emptyProfessorForm` 增加：

```typescript
personal_note: "",
```

`toProfessorForm` 增加：

```typescript
personal_note: professor.personal_note ?? "",
```

`toProfessorPayload` 增加：

```typescript
personal_note: form.personal_note.trim() || null,
```

增加 state：

```tsx
const [noteEditorProfessor, setNoteEditorProfessor] =
  useState<ProfessorManagementItemDTO | null>(null);
const [savingProfessorNote, setSavingProfessorNote] = useState(false);
```

增加保存函数：

```tsx
const saveProfessorNote = async (note: string) => {
  if (!noteEditorProfessor) {
    return;
  }
  setSavingProfessorNote(true);
  try {
    const updated = await updateProfessorNote(noteEditorProfessor.id, note);
    setProfessors((previous) =>
      previous.map((professor) =>
        professor.id === updated.id
          ? { ...professor, personal_note: updated.personal_note }
          : professor,
      ),
    );
    setNoteEditorProfessor(null);
    notifySuccess("备注已更新", `已更新“${noteEditorProfessor.name}”的个人备注。`);
  } catch (saveError) {
    const message = getActionErrorMessage(saveError, "保存备注失败");
    notifyError("保存备注失败", message);
  } finally {
    setSavingProfessorNote(false);
  }
};
```

给 `ManagementProfessorRow` 增加：

```tsx
onEditNote={() => setNoteEditorProfessor(professor)}
```

完整编辑弹窗中，在近期论文 textarea 后、主页链接前插入：

```tsx
<label className="block md:col-span-2">
  {renderFieldLabel("个人备注")}
  <textarea
    aria-label="个人备注"
    value={formState.personal_note}
    onChange={(event) =>
      setFormState((previous) => ({
        ...previous,
        personal_note: event.target.value,
      }))
    }
    className="min-h-32 w-full rounded-2xl border border-stone-200 bg-white px-3 py-3 text-sm leading-6 text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15"
    placeholder="例如：6 月 20 日上午 Zoom 面试；导师回复很积极；后续需要补发成绩单。"
  />
</label>
```

导出弹窗中，紧跟“字段顺序与导入模板一致...”文案后加：

```tsx
<p className="mt-2 text-sm leading-6 text-amber-700">
  导出文件包含个人备注，请谨慎分享。
</p>
```

在页面 dialogs 附近渲染 `ProfessorNoteDialog`，同首页：

```tsx
<ProfessorNoteDialog
  open={Boolean(noteEditorProfessor)}
  professor={noteEditorProfessor}
  initialNote={noteEditorProfessor?.personal_note ?? ""}
  saving={savingProfessorNote}
  onSave={(note) => void saveProfessorNote(note)}
  onClose={() => {
    if (!savingProfessorNote) {
      setNoteEditorProfessor(null);
    }
  }}
/>
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd frontend && npm run test:dom -- ProfessorsPageLayout.test.tsx
```

预期：PASS。

- [ ] **步骤 5：提交**

```bash
rtk git add frontend/src/pages/ProfessorsPage.tsx frontend/test/ProfessorsPageLayout.test.tsx
rtk git commit -m "feat(frontend): manage professor personal notes"
```

---

### 任务 11：回归保护和隐私边界

**文件：**
- 修改：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts`
- 修改：`frontend/src/features/professor-management/client/filterManagementProfessors.test.ts`

- [ ] **步骤 1：编写边界测试**

在 `frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts` 的 keyword 搜索测试附近新增：

```typescript
it("does not search personal notes from dashboard keyword filters", () => {
  const professors = [
    createProfessor({ name: "Alice", personal_note: "独有备注关键词" }),
    createProfessor({ name: "Bob", personal_note: null }),
  ];

  expect(namesFor(professors, { keyword: "独有备注关键词" })).toEqual([]);
});
```

在同一步中把该测试文件的 `createProfessor` helper 调整为支持 `personal_note` override：确认 helper 的入参是 `overrides: Partial<ProfessorDashboardItemDTO>`，再在测试中传入 `personal_note`。

在 `frontend/src/features/professor-management/client/filterManagementProfessors.test.ts` 新增同类测试：

```typescript
it("does not search personal notes from management keyword filters", () => {
  const professors = [
    createProfessor({ name: "Alice", personal_note: "隐私备注关键词" }),
    createProfessor({ name: "Bob", personal_note: null }),
  ];

  expect(namesFor(professors, { keyword: "隐私备注关键词" })).toEqual([]);
});
```

- [ ] **步骤 2：运行测试**

运行：

```bash
cd frontend && npm run test:node -- filterDashboardProfessors.test.ts filterManagementProfessors.test.ts
```

预期：PASS。失败时执行步骤 3，移除过滤函数对 `personal_note` 的读取。

- [ ] **步骤 3：确认过滤逻辑不读取备注**

确认 `DASHBOARD_KEYWORD_SEARCH_SCOPE_OPTIONS` 和 `MANAGEMENT_KEYWORD_SEARCH_SCOPE_OPTIONS` 不包含备注。确认 `getDashboardKeywordValue` 和 `getManagementKeywordValue` 没有 `personal_note` 分支。

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd frontend && npm run test:node -- filterDashboardProfessors.test.ts filterManagementProfessors.test.ts
```

预期：PASS。

- [ ] **步骤 5：提交**

```bash
rtk git add frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts frontend/src/features/professor-management/client/filterManagementProfessors.test.ts
rtk git commit -m "test(frontend): protect professor note privacy boundaries"
```

---

### 任务 12：最终验证

**文件：**
- 修改：本任务不计划修改代码文件；验证失败时回到对应任务修复并提交。

- [ ] **步骤 1：运行后端教授相关测试**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_professor_management
```

预期：PASS。

- [ ] **步骤 2：运行后端 API 重点测试**

运行：

```bash
cd backend && uv run python -m unittest backend.test.test_api_endpoints.ApiEndpointTests.test_professor_personal_note_create_list_update_and_clear backend.test.test_api_endpoints.ApiEndpointTests.test_update_professor_personal_note_returns_404_for_missing_professor backend.test.test_api_endpoints.ApiEndpointTests.test_professor_template_download_and_import_file_upserts_existing_records
```

预期：PASS。

- [ ] **步骤 3：运行前端组件和页面测试**

运行：

```bash
cd frontend && npm run test:dom -- ProfessorNoteButton.test.tsx ProfessorNoteDialog.test.tsx DashboardProfessorRow.test.tsx ManagementProfessorRow.test.tsx ProfessorsPageLayout.test.tsx HomePageProfessorNotes.test.tsx
```

预期：PASS。

- [ ] **步骤 4：运行前端类型检查和 lint**

运行：

```bash
cd frontend && npm run build && npm run lint
```

预期：PASS。

- [ ] **步骤 5：运行迁移检查**

运行：

```bash
cd backend && uv run alembic upgrade head
```

预期：PASS，数据库可升级到 `20260612profnote`。

- [ ] **步骤 6：检查 git 状态**

运行：

```bash
rtk git status --short
```

预期：只剩下本功能已提交内容之外的既有无关改动。

- [ ] **步骤 7：记录最终状态**

```bash
rtk git log --oneline -5
```

预期：最近提交包含本计划任务的功能提交。步骤 1-6 发现需要代码修复时，回到对应任务补测试、修复、运行验证并提交。

---

## 规格覆盖自检

- 列表只在有备注时显示图标：任务 6、8、9、10。
- hover / focus 显示完整备注：任务 6。
- 点击图标打开只编辑备注弹窗：任务 7、9、10。
- 清空备注后图标消失：任务 3、9、10。
- 导师管理完整编辑弹窗支持备注：任务 10。
- 后端字段、DTO、专用接口：任务 1、3。
- CSV / XLSX 模板、导入、导出支持备注：任务 2、4。
- 旧模板兼容且不破坏已有备注：任务 2、4。
- 备注不进入 AI / 筛选 / 邮件生成：任务 11 覆盖前端筛选边界；后端计划未改 LLM 和邮件上下文。
- 导出隐私提示：任务 10。
