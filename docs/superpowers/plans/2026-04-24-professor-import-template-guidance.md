# 导师导入模板说明增强实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 CSV/XLSX 导师导入模板自带字段说明和示例行，并让导入系统自动忽略说明行和未删除的示例行。

**架构：** 后端在 `professor_management.py` 中集中维护模板字段说明、示例数据和解析容错规则。前端只展示轻提示，不复制完整字段字典。测试覆盖模板下载、CSV/XLSX 导入容错和导入弹窗提示。

**技术栈：** FastAPI、Python `csv`、openpyxl、unittest、React、Vitest、Testing Library。

---

## 文件结构

- 修改：`backend/app/services/professor_management.py`
  - 职责：生成带说明/示例的 CSV/XLSX 模板；解析导入文件时自动定位表头并跳过说明/示例行。
- 修改：`backend/test/test_api_endpoints.py`
  - 职责：覆盖模板下载内容和带说明/示例行的 CSV/XLSX 导入行为。
- 修改：`frontend/src/pages/ProfessorsPage.tsx`
  - 职责：导入弹窗提示模板内置说明、示例行可保留、`recent_papers` 分隔规则。
- 修改：`frontend/test/ProfessorsPageNotifications.test.tsx`
  - 职责：覆盖导入弹窗关键提示。

## 任务 1：后端模板与解析测试

**文件：**
- 修改：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写失败的测试**

在 `test_professor_template_download_and_import_file_upserts_existing_records` 中增强模板下载断言：

```python
self.assertIn("# 导师导入模板", csv_template.text)
self.assertIn("# name：导师姓名，必填。示例：张明远", csv_template.text)
self.assertIn("示例：张明远,zhang@example.edu", csv_template.text)

workbook_from_template = load_workbook(io.BytesIO(xlsx_template.content))
template_sheet = workbook_from_template.active
template_values = [
    [cell for cell in row]
    for row in template_sheet.iter_rows(values_only=True)
]
self.assertIn("# 导师导入模板", template_values[0][0])
self.assertIn("# name：导师姓名，必填。示例：张明远", template_values[3][0])
self.assertIn("示例：张明远", [cell.value for cell in template_sheet[15]][0])
```

新增 CSV 导入内容，保留说明行和示例行，预期示例行不计入失败数：

```python
csv_content = (
    "# 导师导入模板\n"
    "# 从字段名下一行开始填写；说明行和示例行可以保留，系统导入时会自动忽略\n"
    "# 必填字段：name, email\n"
    "name,email,title,university,school,department,research_direction,recent_papers,profile_url,source_url\n"
    "示例：张明远,zhang@example.edu,教授,示例大学,人工智能学院,计算机科学系,大语言模型；智能体；信息抽取,Paper 1|Paper 2,https://example.edu/zhang,https://example.edu/faculty\n"
    "李教授,li@example.edu,Associate Professor,New University,School of AI,AI,Updated direction,Paper 1|Paper 2,https://real.edu/li,https://real.edu/faculty\n"
    "王老师,wang@example.edu,Assistant Professor,Another University,School,Dept,Direction,Paper 3,,\n"
    "坏数据,not-an-email,Professor,Bad University,School,Dept,Direction,Paper X,,\n"
).encode("utf-8-sig")
```

新增 XLSX 导入内容，前置说明行并保留示例行：

```python
sheet.append(["# 导师导入模板"])
sheet.append(["# 从字段名下一行开始填写；说明行和示例行可以保留，系统导入时会自动忽略"])
sheet.append(["# 必填字段：name, email"])
sheet.append([
    "name",
    "email",
    "title",
    "university",
    "school",
    "department",
    "research_direction",
    "recent_papers",
    "profile_url",
    "source_url",
])
sheet.append([
    "示例：张明远",
    "zhang@example.edu",
    "教授",
    "示例大学",
    "人工智能学院",
    "计算机科学系",
    "大语言模型；智能体；信息抽取",
    "Paper A|Paper B",
    "https://example.edu/zhang",
    "https://example.edu/faculty",
])
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_professor_template_download_and_import_file_upserts_existing_records`

预期：FAIL。当前 CSV/XLSX 模板不含说明和示例；带说明行的导入无法识别表头或把示例行计入失败。

## 任务 2：后端实现

**文件：**
- 修改：`backend/app/services/professor_management.py`

- [ ] **步骤 1：编写最少实现代码**

新增字段说明和示例常量：

```python
PROFESSOR_TEMPLATE_HELP_LINES = [
    "# 导师导入模板",
    "# 从字段名下一行开始填写；说明行和示例行可以保留，系统导入时会自动忽略",
    "# 必填字段：name, email",
    "# name：导师姓名，必填。示例：张明远",
    "# email：导师邮箱，必填，必须是邮箱格式。示例：zhang@example.edu",
    "# title：导师职称。示例：教授",
    "# university：学校名称。示例：示例大学",
    "# school：学院名称。示例：人工智能学院",
    "# department：院系或系所。示例：计算机科学系",
    "# research_direction：研究方向，多个方向用中文分号 ； 分隔。示例：大语言模型；智能体；信息抽取",
    "# recent_papers：近期论文，多篇用 | 分隔。示例：Paper A|Paper B",
    "# profile_url：导师主页链接。示例：https://example.edu/zhang",
    "# source_url：数据来源链接。示例：https://example.edu/faculty",
]

PROFESSOR_TEMPLATE_EXAMPLE_ROW = [
    "示例：张明远",
    "zhang@example.edu",
    "教授",
    "示例大学",
    "人工智能学院",
    "计算机科学系",
    "大语言模型；智能体；信息抽取",
    "Paper A|Paper B",
    "https://example.edu/zhang",
    "https://example.edu/faculty",
]
```

CSV 模板先写说明行，再写表头和示例行。XLSX 模板同样写说明、表头和示例，并对说明行和表头做轻样式。

新增 `_find_header_row_index()`、`_is_help_row()`、`_should_skip_import_row()`，让 CSV 和 XLSX 解析共用：

```python
def _find_header_row_index(rows: list[list[Any]]) -> int:
    for index, row in enumerate(rows):
        normalized = [str(item).strip() if item is not None else "" for item in row]
        if all(column in normalized for column in PROFESSOR_TEMPLATE_COLUMNS):
            return index
    raise ValueError("导入文件缺少必要列：" + ", ".join(PROFESSOR_TEMPLATE_COLUMNS))
```

导入循环在调用 `_normalize_import_row()` 前跳过空行、说明行和 `name` 以 `示例：` 开头的模板示例行：

```python
if _should_skip_import_row(row):
    continue
```

- [ ] **步骤 2：运行后端目标测试验证通过**

运行：`cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_professor_template_download_and_import_file_upserts_existing_records`

预期：PASS。

## 任务 3：前端提示测试与实现

**文件：**
- 修改：`frontend/test/ProfessorsPageNotifications.test.tsx`
- 修改：`frontend/src/pages/ProfessorsPage.tsx`

- [ ] **步骤 1：编写失败的前端测试**

在 `ProfessorsPage notifications` 中新增测试：

```typescript
it("explains that import templates include guidance and ignored examples", async () => {
  renderPage();

  await waitFor(() => {
    expect(listProfessorsForManagement).toHaveBeenCalled();
  });

  fireEvent.click(
    within(getWorkbenchRegion()).getByRole("button", {
      name: "导入文件",
    }),
  );

  expect(screen.getByText(/模板内已包含字段说明和示例行/)).toBeInTheDocument();
  expect(screen.getByText(/示例行可以保留，导入时会自动忽略/)).toBeInTheDocument();
  expect(screen.getByText(/recent_papers/)).toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm test -- ProfessorsPageNotifications.test.tsx`

预期：FAIL，找不到新增提示文案。

- [ ] **步骤 3：实现前端提示**

在导入弹窗左侧替换旧说明段落和字段胶囊，使用简短要点：

```tsx
<ul className="mt-4 space-y-3 text-sm leading-6 text-stone-600">
  <li>模板内已包含字段说明和示例行，下载后可直接照着填写。</li>
  <li>说明行和示例行可以保留，导入时会自动忽略。</li>
  <li>
    <span className="font-mono text-xs">recent_papers</span> 多篇论文用 |
    分隔；同邮箱会覆盖更新。
  </li>
</ul>
```

右侧上传区补充回收站恢复规则：

```tsx
必填列是 name 和 email。格式错误的行会跳过；同邮箱记录会覆盖更新，回收站记录会自动恢复。
```

- [ ] **步骤 4：运行前端目标测试验证通过**

运行：`cd frontend && npm test -- ProfessorsPageNotifications.test.tsx`

预期：PASS。

## 任务 4：完整验证与收尾

**文件：**
- 修改：`backend/app/services/professor_management.py`
- 修改：`backend/test/test_api_endpoints.py`
- 修改：`frontend/src/pages/ProfessorsPage.tsx`
- 修改：`frontend/test/ProfessorsPageNotifications.test.tsx`

- [ ] **步骤 1：运行相关后端测试**

运行：`cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_professor_template_download_and_import_file_upserts_existing_records`

预期：PASS。

- [ ] **步骤 2：运行相关前端测试**

运行：`cd frontend && npm test -- ProfessorsPageNotifications.test.tsx`

预期：PASS。

- [ ] **步骤 3：检查差异**

运行：`git diff -- backend/app/services/professor_management.py backend/test/test_api_endpoints.py frontend/src/pages/ProfessorsPage.tsx frontend/test/ProfessorsPageNotifications.test.tsx`

预期：只包含导师导入模板说明增强相关改动。

- [ ] **步骤 4：提交本任务改动**

如果目标文件存在用户已有改动，使用部分暂存，只提交本任务相关 hunk。

提交信息：

```bash
git commit -m "feat(导师导入): 增强模板说明和示例容错"
```
