# 导出导师信息实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在导师档案管理页新增「导出导师信息」功能，支持将全部正常导师导出为可原样再导入的 XLSX 或 CSV 文件。

**架构：** 后端在现有导师管理服务中新增导出构建函数，复用导入模板列定义，新增 `/api/professors/export` 下载接口。前端在现有导师管理页的「导师录入方式」面板右侧增加导出卡片，点击后打开弹窗选择导出格式，并复用无空白页的 `triggerDownload` 下载方式。

**技术栈：** FastAPI、SQLAlchemy AsyncSession、openpyxl、Python unittest、React、TypeScript、Vitest、Testing Library。

---

## 文件结构

- 修改：`backend/app/services/professor_management.py`
  - 新增 `build_professor_export` 和行转换辅助函数。
  - 复用 `PROFESSOR_TEMPLATE_COLUMNS`，保证导出字段与导入模板一致。
- 修改：`backend/app/api/professors.py`
  - 引入 `build_professor_export`。
  - 新增 `GET /api/professors/export?format=xlsx|csv`。
  - 查询 `archived_at is None` 的全部正常导师。
- 修改：`backend/test/test_professor_management.py`
  - 覆盖 CSV / XLSX 导出、原样再导入、空数据、格式校验。
- 修改：`frontend/src/lib/api/professorsApi.ts`
  - 新增 `getProfessorExportDownloadUrl(format)`。
- 修改：`frontend/src/pages/ProfessorsPage.tsx`
  - 新增导出弹窗状态和处理函数。
  - 调整「导师录入方式」区域为左侧 3 张录入卡片、右侧导出卡片。
  - 新增导出弹窗，提供 XLSX 和 CSV 下载按钮。
- 修改：`frontend/test/ProfessorsPageLayout.test.tsx`
  - Mock 导出下载 URL。
  - 覆盖入口展示、弹窗内容、下载触发和不打开空白页。

---

### 任务 1：后端导出构建函数

**文件：**
- 修改：`backend/app/services/professor_management.py`
- 测试：`backend/test/test_professor_management.py`

- [ ] **步骤 1：编写失败的服务测试**

在 `backend/test/test_professor_management.py` 的 import 中加入 `load_workbook`、`Professor` 和 `build_professor_export`：

```python
from openpyxl import Workbook, load_workbook

from app.models import Professor
from app.services.professor_management import (
    PROFESSOR_TEMPLATE_COLUMNS,
    build_professor_export,
    build_professor_template,
    is_valid_professor_email,
    normalize_professor_email,
    normalize_professor_payload,
    parse_professor_import_file,
)
```

在 `ProfessorManagementServiceTests` 中新增测试：

```python
    def test_build_professor_export_csv_can_be_imported_without_changes(self) -> None:
        professor = Professor(
            name="李伟",
            email="li@example.edu",
            title="教授",
            university="示例大学",
            school="人工智能学院",
            department="计算机科学系",
            research_direction="大语言模型",
            recent_papers=["Paper A", "Paper B"],
            profile_url="https://example.edu/li",
            source_url=None,
        )

        content, media_type, filename = build_professor_export([professor], "csv")

        self.assertEqual(media_type, "text/csv; charset=utf-8")
        self.assertEqual(filename, "professors_export.csv")
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
        decoded = content.decode("utf-8-sig")
        self.assertIn(",".join(PROFESSOR_TEMPLATE_COLUMNS), decoded)
        self.assertIn("Paper A|Paper B", decoded)
        self.assertNotIn("None", decoded)
        self.assertNotIn("null", decoded)

        parsed = parse_professor_import_file(filename, content)
        self.assertEqual(parsed.failed_count, 0)
        self.assertEqual(parsed.data["li@example.edu"]["name"], "李伟")
        self.assertEqual(parsed.data["li@example.edu"]["recent_papers"], ["Paper A", "Paper B"])

    def test_build_professor_export_xlsx_can_be_imported_without_changes(self) -> None:
        professor = Professor(
            name="王芳",
            email="wang@example.edu",
            title="副教授",
            university="样例大学",
            school="生命科学学院",
            department="生物信息系",
            research_direction="计算生物学",
            recent_papers=["Paper C"],
            profile_url=None,
            source_url="https://example.edu/faculty",
        )

        content, media_type, filename = build_professor_export([professor], "xlsx")

        self.assertEqual(
            media_type,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertEqual(filename, "professors_export.xlsx")
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        rows = list(workbook.active.iter_rows(values_only=True))
        self.assertEqual(list(rows[0]), PROFESSOR_TEMPLATE_COLUMNS)
        self.assertEqual(rows[1][0], "王芳")
        self.assertEqual(rows[1][7], "Paper C")
        self.assertIsNone(rows[1][8])

        parsed = parse_professor_import_file(filename, content)
        self.assertEqual(parsed.failed_count, 0)
        self.assertEqual(parsed.data["wang@example.edu"]["source_url"], "https://example.edu/faculty")

    def test_build_professor_export_empty_file_and_unknown_format(self) -> None:
        csv_content, _, csv_filename = build_professor_export([], "csv")
        parsed_csv = parse_professor_import_file(csv_filename, csv_content)
        self.assertEqual(parsed_csv.failed_count, 0)
        self.assertEqual(parsed_csv.data, {})

        xlsx_content, _, xlsx_filename = build_professor_export([], "xlsx")
        parsed_xlsx = parse_professor_import_file(xlsx_filename, xlsx_content)
        self.assertEqual(parsed_xlsx.failed_count, 0)
        self.assertEqual(parsed_xlsx.data, {})

        with self.assertRaisesRegex(ValueError, "仅支持 csv 或 xlsx 导出"):
            build_professor_export([], "json")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_professor_management.ProfessorManagementServiceTests.test_build_professor_export_csv_can_be_imported_without_changes test.test_professor_management.ProfessorManagementServiceTests.test_build_professor_export_xlsx_can_be_imported_without_changes test.test_professor_management.ProfessorManagementServiceTests.test_build_professor_export_empty_file_and_unknown_format
```

预期：FAIL，报错包含 `ImportError` 或 `cannot import name 'build_professor_export'`。

- [ ] **步骤 3：实现导出构建函数**

在 `backend/app/services/professor_management.py` 顶部 import 中加入：

```python
from collections.abc import Sequence
```

在 `build_professor_template` 后、`parse_professor_import_file` 前新增：

```python
def build_professor_export(professors: Sequence[Any], format_name: str) -> tuple[bytes, str, str]:
    normalized = format_name.lower()
    rows = [_professor_to_export_row(professor) for professor in professors]
    if normalized == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(PROFESSOR_TEMPLATE_COLUMNS)
        writer.writerows(rows)
        content = buffer.getvalue().encode("utf-8-sig")
        return content, "text/csv; charset=utf-8", "professors_export.csv"

    if normalized == "xlsx":
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Professors"
        header_fill = PatternFill("solid", fgColor="E7E5E4")
        sheet.append(PROFESSOR_TEMPLATE_COLUMNS)
        for index, column in enumerate(PROFESSOR_TEMPLATE_COLUMNS, start=1):
            cell = sheet.cell(row=1, column=index)
            cell.value = column
            cell.font = Font(bold=True)
            cell.fill = header_fill
            sheet.column_dimensions[chr(64 + index)].width = 22
        for row in rows:
            sheet.append(row)
        sheet.freeze_panes = "A2"
        buffer = io.BytesIO()
        workbook.save(buffer)
        return (
            buffer.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "professors_export.xlsx",
        )

    raise ValueError("仅支持 csv 或 xlsx 导出")


def _professor_to_export_row(professor: Any) -> list[str]:
    recent_papers = getattr(professor, "recent_papers", None) or []
    return [
        _export_cell(getattr(professor, "name", None)),
        _export_cell(getattr(professor, "email", None)),
        _export_cell(getattr(professor, "title", None)),
        _export_cell(getattr(professor, "university", None)),
        _export_cell(getattr(professor, "school", None)),
        _export_cell(getattr(professor, "department", None)),
        _export_cell(getattr(professor, "research_direction", None)),
        "|".join(_export_cell(item) for item in recent_papers if _export_cell(item)),
        _export_cell(getattr(professor, "profile_url", None)),
        _export_cell(getattr(professor, "source_url", None)),
    ]


def _export_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_professor_management.ProfessorManagementServiceTests.test_build_professor_export_csv_can_be_imported_without_changes test.test_professor_management.ProfessorManagementServiceTests.test_build_professor_export_xlsx_can_be_imported_without_changes test.test_professor_management.ProfessorManagementServiceTests.test_build_professor_export_empty_file_and_unknown_format
```

预期：PASS，3 个测试通过。

- [ ] **步骤 5：Commit**

```powershell
git add backend/app/services/professor_management.py backend/test/test_professor_management.py
git commit -m "feat(backend): add professor export builder"
```

---

### 任务 2：后端导出 API

**文件：**
- 修改：`backend/app/api/professors.py`
- 测试：`backend/test/test_professors_api.py` 或现有教授 API 测试文件

- [ ] **步骤 1：定位教授 API 测试文件**

运行：

```powershell
rg -n "TestClient|AsyncClient|/api/professors|list_professors_for_management|download_professor_template" backend/test
```

如果已有教授 API 测试文件，继续修改该文件；如果没有，创建 `backend/test/test_professors_api.py` 并复用项目中其他 API 测试的数据库 fixture 模式。

- [ ] **步骤 2：编写失败的 API 测试**

在教授 API 测试文件中新增以下测试逻辑。若测试基类或 fixture 名称与现有项目不同，保持断言内容不变，按现有 API 测试模式调整 setup：

```python
async def test_export_professors_returns_only_active_professors(client, async_session):
    active = Professor(
        name="李伟",
        email="li@example.edu",
        title="教授",
        university="示例大学",
        recent_papers=["Paper A"],
    )
    archived = Professor(
        name="归档导师",
        email="archived@example.edu",
        archived_at=datetime.now(UTC),
    )
    async_session.add_all([active, archived])
    await async_session.commit()

    response = await client.get("/api/professors/export?format=csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert b"\xef\xbb\xbf" == response.content[:3]
    decoded = response.content.decode("utf-8-sig")
    assert "li@example.edu" in decoded
    assert "archived@example.edu" not in decoded


def test_export_professors_rejects_unknown_format(client):
    response = client.get("/api/professors/export?format=json")
    assert response.status_code == 400
    assert "仅支持 csv 或 xlsx 导出" in response.json()["detail"]
```

- [ ] **步骤 3：运行测试验证失败**

运行对应测试文件，例如：

```powershell
cd backend
uv run python -m unittest test.test_professors_api
```

预期：FAIL，`/api/professors/export` 返回 404 或导入函数不存在。

- [ ] **步骤 4：实现 API 路由**

在 `backend/app/api/professors.py` 的服务 import 中加入：

```python
    build_professor_export,
```

在 `/template` 路由后、`/import-file` 路由前新增：

```python
@router.get("/export")
async def export_professors(
    format: str = Query(default="xlsx"),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    professors = list(
        (
            await session.execute(
                select(Professor)
                .where(Professor.archived_at.is_(None))
                .order_by(Professor.updated_at.desc(), Professor.created_at.desc()),
            )
        ).scalars(),
    )
    try:
        content, media_type, filename = build_professor_export(professors, format)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
```

- [ ] **步骤 5：运行 API 测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_professors_api
```

预期：PASS，新增导出 API 测试通过。

- [ ] **步骤 6：运行后端教授管理测试**

运行：

```powershell
cd backend
uv run python -m unittest test.test_professor_management
```

预期：PASS，教授管理服务测试全部通过。

- [ ] **步骤 7：Commit**

```powershell
git add backend/app/api/professors.py backend/test/test_professors_api.py backend/test/test_professor_management.py
git commit -m "feat(backend): expose professor export endpoint"
```

---

### 任务 3：前端 API helper 和页面交互测试

**文件：**
- 修改：`frontend/src/lib/api/professorsApi.ts`
- 修改：`frontend/test/ProfessorsPageLayout.test.tsx`

- [ ] **步骤 1：编写失败的前端测试**

在 `frontend/test/ProfessorsPageLayout.test.tsx` 顶部 hoisted mock 区增加：

```typescript
const getProfessorExportDownloadUrl = vi.hoisted(() => vi.fn());
```

在 `vi.mock("@/lib/api/professorsApi", ...)` 中加入：

```typescript
  getProfessorExportDownloadUrl,
```

在 `beforeEach` 中加入：

```typescript
    getProfessorExportDownloadUrl.mockReset();
    getProfessorExportDownloadUrl.mockImplementation(
      (format: "xlsx" | "csv") => `/exports/professors.${format}`,
    );
```

新增测试：

```typescript
  it("opens professor export dialog and downloads without opening a blank window", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(screen.getByRole("button", { name: "导出导师信息" }));

    expect(screen.getByRole("dialog", { name: "导出导师信息" })).toBeInTheDocument();
    expect(screen.getByText("导出范围：全部正常导师，不包含回收站导师。")).toBeInTheDocument();
    expect(
      screen.getByText("当前搜索、筛选、分页和勾选状态不会影响导出结果。"),
    ).toBeInTheDocument();

    const link = document.createElement("a");
    const click = vi.spyOn(link, "click").mockImplementation(() => undefined);
    const createElement = vi.spyOn(document, "createElement").mockReturnValue(link);

    fireEvent.click(screen.getByRole("button", { name: "导出 XLSX" }));

    expect(getProfessorExportDownloadUrl).toHaveBeenCalledWith("xlsx");
    expect(createElement).toHaveBeenCalledWith("a");
    expect(link).toHaveAttribute("href", "/exports/professors.xlsx");
    expect(link).not.toHaveAttribute("target");
    expect(link).not.toHaveAttribute("rel");
    expect(click).toHaveBeenCalledTimes(1);
  });
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd frontend
npm.cmd run test:dom -- ProfessorsPageLayout.test.tsx
```

预期：FAIL，找不到「导出导师信息」按钮或 `getProfessorExportDownloadUrl` 未实现。

- [ ] **步骤 3：新增前端 API helper**

在 `frontend/src/lib/api/professorsApi.ts` 末尾加入：

```typescript
export const getProfessorExportDownloadUrl = (format: 'xlsx' | 'csv') =>
  buildApiUrl('/api/professors/export', { format });
```

- [ ] **步骤 4：运行测试确认仍因页面缺入口失败**

运行：

```powershell
cd frontend
npm.cmd run test:dom -- ProfessorsPageLayout.test.tsx
```

预期：FAIL，API helper 相关错误消失，但仍找不到导出按钮或弹窗。

- [ ] **步骤 5：Commit**

```powershell
git add frontend/src/lib/api/professorsApi.ts frontend/test/ProfessorsPageLayout.test.tsx
git commit -m "test(frontend): cover professor export dialog download"
```

---

### 任务 4：前端导师管理页导出 UI

**文件：**
- 修改：`frontend/src/pages/ProfessorsPage.tsx`
- 测试：`frontend/test/ProfessorsPageLayout.test.tsx`

- [ ] **步骤 1：补齐 imports 和状态**

在 `frontend/src/pages/ProfessorsPage.tsx` 的 lucide-react import 中复用已有 `Download` 和 `FileSpreadsheet`，无需新增图标。如果想让导出卡片更明确，可加入 `FileDown`：

```typescript
  FileDown,
```

在 professors API import 中加入：

```typescript
  getProfessorExportDownloadUrl,
```

在组件状态区新增：

```typescript
  const [exportModalOpen, setExportModalOpen] = useState(false);
```

- [ ] **步骤 2：新增导出处理函数**

在 `handleDownloadTemplate` 附近新增：

```typescript
  const handleDownloadExport = (format: "xlsx" | "csv") => {
    triggerDownload(getProfessorExportDownloadUrl(format));
  };
```

不得修改 `triggerDownload` 为 `_blank` 下载；必须保持不设置 `target` 和 `rel`。

- [ ] **步骤 3：调整导师录入方式布局**

将现有：

```tsx
              <div className="grid gap-3 lg:grid-cols-3">
                <IntakeActionCard ...>...</IntakeActionCard>
                <IntakeActionCard ...>...</IntakeActionCard>
                <IntakeActionCard ...>...</IntakeActionCard>
              </div>
```

改为：

```tsx
              <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(13rem,0.28fr)]">
                <div className="grid gap-3 lg:grid-cols-3">
                  <IntakeActionCard ...>...</IntakeActionCard>
                  <IntakeActionCard ...>...</IntakeActionCard>
                  <IntakeActionCard ...>...</IntakeActionCard>
                </div>
                <div className="rounded-[28px] border border-emerald-200 bg-gradient-to-b from-emerald-50 to-white p-4 shadow-sm">
                  <div className="flex h-full flex-col justify-between gap-4">
                    <div>
                      <div className="flex items-center gap-2 text-sm font-semibold text-emerald-800">
                        <Download className="h-4 w-4" />
                        导出导师信息
                      </div>
                      <p className="mt-2 text-sm leading-6 text-emerald-700/80">
                        导出全部正常导师，字段与导入模板一致。
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => setExportModalOpen(true)}
                      className="ui-btn-primary h-10 rounded-2xl bg-emerald-600 hover:bg-emerald-700"
                    >
                      <Download className="h-4 w-4" />
                      导出导师信息
                    </button>
                  </div>
                </div>
              </div>
```

保留 3 个现有 `IntakeActionCard` 的内部行为和按钮文案，不改变模板导入弹窗。

- [ ] **步骤 4：新增导出弹窗**

在导入弹窗 `ModalShell` 之前或之后新增：

```tsx
      <ModalShell
        open={exportModalOpen}
        title="导出导师信息"
        description="将全部正常导师导出为表格文件。字段顺序与导入模板保持一致，便于备份、外部整理或修改后再次导入。"
        onClose={() => setExportModalOpen(false)}
      >
        <div className="mt-6 rounded-[28px] border border-stone-200 bg-white p-5 shadow-sm">
          <div className="text-sm font-semibold text-stone-900">选择导出格式</div>
          <p className="mt-2 text-sm leading-6 text-stone-500">
            推荐使用 XLSX 直接在表格软件中查看；CSV 适合脚本处理和跨工具交换。
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => handleDownloadExport("xlsx")}
              className="ui-btn-primary"
            >
              <FileSpreadsheet className="h-4 w-4" />
              导出 XLSX
            </button>
            <button
              type="button"
              onClick={() => handleDownloadExport("csv")}
              className="ui-btn-secondary"
            >
              <Download className="h-4 w-4" />
              导出 CSV
            </button>
          </div>
          <ul className="mt-5 space-y-2 text-sm leading-6 text-stone-600">
            <li>导出范围：全部正常导师，不包含回收站导师。</li>
            <li>当前搜索、筛选、分页和勾选状态不会影响导出结果。</li>
            <li>字段顺序与导入模板一致，未修改即可重新导入系统。</li>
            <li>空值会保留为空单元格，CSV 使用 UTF-8 编码。</li>
          </ul>
        </div>
      </ModalShell>
```

- [ ] **步骤 5：运行前端测试验证通过**

运行：

```powershell
cd frontend
npm.cmd run test:dom -- ProfessorsPageLayout.test.tsx
```

预期：PASS，包含新增导出弹窗测试和原模板下载不打开空白页测试。

- [ ] **步骤 6：运行前端 lint**

运行：

```powershell
cd frontend
npm.cmd run lint -- src/pages/ProfessorsPage.tsx src/lib/api/professorsApi.ts test/ProfessorsPageLayout.test.tsx
```

预期：PASS，无 ESLint 错误。

- [ ] **步骤 7：Commit**

```powershell
git add frontend/src/pages/ProfessorsPage.tsx frontend/src/lib/api/professorsApi.ts frontend/test/ProfessorsPageLayout.test.tsx
git commit -m "feat(frontend): add professor export dialog"
```

---

### 任务 5：端到端验证和回归检查

**文件：**
- 验证：`backend/app/services/professor_management.py`
- 验证：`backend/app/api/professors.py`
- 验证：`frontend/src/pages/ProfessorsPage.tsx`

- [ ] **步骤 1：运行完整后端相关测试**

运行：

```powershell
cd backend
uv run python -m unittest test.test_professor_management test.test_professors_api
```

预期：PASS，教授导入、模板、导出 API 相关测试全部通过。

- [ ] **步骤 2：运行前端相关测试**

运行：

```powershell
cd frontend
npm.cmd run test:dom -- ProfessorsPageLayout.test.tsx
```

预期：PASS，导师页面布局测试全部通过。

- [ ] **步骤 3：运行前端 lint**

运行：

```powershell
cd frontend
npm.cmd run lint -- src/pages/ProfessorsPage.tsx src/lib/api/professorsApi.ts test/ProfessorsPageLayout.test.tsx
```

预期：PASS，无 lint 错误。

- [ ] **步骤 4：手动检查下载 URL 行为**

启动后端和前端：

```powershell
cd backend
uv run python dev_entry.py
```

另开终端：

```powershell
cd frontend
npm.cmd run dev
```

在浏览器打开导师档案管理页，点击「导出导师信息」后点击「导出 XLSX」和「导出 CSV」。预期：浏览器触发文件下载，不打开空白页面；下载文件可通过「模板导入」弹窗原样导入。

- [ ] **步骤 5：最终 Commit**

如果任务 1-4 已分别提交，本步骤只提交验证中发现的必要修正：

```powershell
git status --short
git add <必要修正文件>
git commit -m "test: verify professor export regression coverage"
```

如果没有额外修正，不创建空 commit。
