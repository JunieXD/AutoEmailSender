# 导师标签功能实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为导师库添加正式的多标签功能，支持自定义标签、展示、编辑、删除、关键词搜索和标签筛选。

**架构：** 后端新增 `ProfessorTag` 和 `ProfessorTagLink` 两张表，通过 SQLAlchemy relationship 加载导师标签，并在教授 DTO、创建/更新接口和导出逻辑中携带标签。前端新增标签 API、标签胶囊和标签选择组件，把标签接入首页、导师管理页、编辑弹窗、关键词搜索范围和高级筛选。

**技术栈：** FastAPI、SQLAlchemy、Alembic、Pydantic、unittest、React、TypeScript、Vitest、Testing Library、Tailwind CSS。

**执行状态（2026-06-06）：** 任务 1-6 已实现并提交。最终验证：
- 后端：`.\.venv\Scripts\python.exe -m unittest test.test_professor_tags test.test_professor_management`，19 tests OK。
- 前端测试：`npm.cmd test -- src/components/molecules/ProfessorTagChips.test.tsx src/components/molecules/ProfessorTagSelector.test.tsx src/components/molecules/DashboardProfessorRow.test.tsx src/components/molecules/ManagementProfessorRow.test.tsx src/features/home-dashboard/client/filterDashboardProfessors.test.ts src/features/professor-management/client/filterManagementProfessors.test.ts src/features/home-dashboard/client/sortDashboardProfessors.test.ts src/features/professor-management/client/sortManagementProfessors.test.ts`，8 files / 44 tests passed。
- 静态检查：`npm.cmd run lint` 退出码 0。
- 构建：`npm.cmd run build` 退出码 0。

---

## 文件职责

- `backend/app/models/professor.py`：新增标签模型、关联表模型和 `Professor.tags` relationship。
- `backend/app/models/__init__.py`：导出 `ProfessorTag` 和 `ProfessorTagLink`。
- `backend/alembic/versions/20260606_add_professor_tags.py`：创建标签表和关联表，插入默认标签。
- `backend/app/schemas/professor.py`：新增标签读写 schema，并让教授 DTO 和 upsert payload 携带标签。
- `backend/app/api/professors.py`：新增标签 CRUD，教授列表加载/序列化标签，创建/更新导师保存标签。
- `backend/app/services/professor_management.py`：导出增加 `tags` 列，导入继续忽略 `tags` 列。
- `backend/test/test_professor_tags.py`：覆盖标签 API、默认标签、删除关联和 upsert 标签行为。
- `backend/test/test_professor_management.py`：覆盖导出 `tags` 列。
- `frontend/src/types/index.ts`：新增 `ProfessorTagDTO`、`ProfessorTagPayloadDTO`，教授 DTO 和 upsert payload 增加 tags/tag_ids。
- `frontend/src/lib/api/professorsApi.ts`：新增标签列表、创建、删除 API。
- `frontend/src/components/molecules/ProfessorTagChips.tsx`：展示标签胶囊和 `+N` 摘要。
- `frontend/src/components/molecules/ProfessorTagSelector.tsx`：编辑弹窗中的标签选择、自定义创建和删除入口。
- `frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts`：首页筛选状态增加关键词范围和标签筛选。
- `frontend/src/features/professor-management/client/filterManagementProfessors.ts`：管理页筛选状态增加关键词范围和标签筛选。
- `frontend/src/pages/HomePage.tsx`：加载标签候选，接入标签展示、关键词范围和高级标签筛选。
- `frontend/src/pages/ProfessorsPage.tsx`：加载标签候选，编辑弹窗接入标签选择，管理列表展示和筛选标签。

## 任务 1：后端标签模型、迁移和导出

**文件：**
- 修改：`backend/app/models/professor.py`
- 修改：`backend/app/models/__init__.py`
- 创建：`backend/alembic/versions/20260606_add_professor_tags.py`
- 修改：`backend/app/schemas/professor.py`
- 修改：`backend/app/services/professor_management.py`
- 修改：`backend/test/test_professor_management.py`

- [ ] **步骤 1：编写失败的导出测试**

在 `backend/test/test_professor_management.py` 中添加：

```python
def test_build_professor_export_includes_tags_column_for_view_only(self) -> None:
    tag = type(
        "Tag",
        (),
        {
            "name": "高意愿",
        },
    )()
    professor = Professor(
        name="李伟",
        email="li@example.edu",
        title="教授",
        university="示例大学",
        school="人工智能学院",
        department="计算机科学系",
        research_direction="大语言模型",
        recent_papers=["Paper A"],
        profile_url=None,
        source_url=None,
    )
    professor.tags = [tag]

    content, _, filename = build_professor_export([professor], "csv")

    decoded = content.decode("utf-8-sig")
    self.assertIn("tags", decoded.splitlines()[0])
    self.assertIn("高意愿", decoded)
    parsed = parse_professor_import_file(filename, content)
    self.assertEqual(parsed.failed_count, 0)
    self.assertEqual(parsed.data["li@example.edu"]["name"], "李伟")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_professor_management.ProfessorManagementServiceTests.test_build_professor_export_includes_tags_column_for_view_only
```

预期：FAIL，失败原因是导出表头没有 `tags` 列。

- [ ] **步骤 3：实现模型、schema 和导出最少代码**

在 `backend/app/models/professor.py` 中新增：

```python
from sqlalchemy import ForeignKey, UniqueConstraint

class ProfessorTagLink(Base):
    __tablename__ = "professor_tag_links"
    __table_args__ = (
        UniqueConstraint("professor_id", "tag_id", name="uq_professor_tag_links_professor_tag"),
    )

    professor_id: Mapped[int] = mapped_column(ForeignKey("professors.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("professor_tags.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

class ProfessorTag(Base):
    __tablename__ = "professor_tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    text_color: Mapped[str] = mapped_column(String(16), nullable=False)
    background_color: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"), onupdate=utc_now)
```

并给 `Professor` 添加：

```python
tags: Mapped[list["ProfessorTag"]] = relationship(
    secondary="professor_tag_links",
    order_by="ProfessorTag.name",
    lazy="selectin",
)
```

在 `backend/app/models/__init__.py` 导出新模型。

在 `backend/app/schemas/professor.py` 新增：

```python
class ProfessorTagRead(ApiSchema):
    id: int
    name: str
    text_color: str
    background_color: str

class ProfessorTagPayload(BaseModel):
    name: str
    text_color: str
    background_color: str
```

并给 `ProfessorRead`、`ProfessorDashboardItemRead`、`ProfessorManagementItemRead` 加 `tags: list[ProfessorTagRead] = Field(default_factory=list)`，给 `ProfessorUpsertPayload` 加 `tag_ids: list[int] = Field(default_factory=list)`。

在 `backend/app/services/professor_management.py` 新增 `PROFESSOR_EXPORT_COLUMNS = [*PROFESSOR_TEMPLATE_COLUMNS, "tags"]`，导出表头使用它，导入表头仍按 `PROFESSOR_TEMPLATE_COLUMNS` 校验；`_professor_to_export_row` 末尾追加：

```python
"；".join(_export_cell(tag.name) for tag in getattr(professor, "tags", []) if _export_cell(tag.name)),
```

创建 Alembic 文件 `backend/alembic/versions/20260606_add_professor_tags.py`，`down_revision` 使用当前 head，创建两张表并插入默认标签。

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令。

预期：PASS。

- [ ] **步骤 5：提交任务 1**

```powershell
git add backend/app/models/professor.py backend/app/models/__init__.py backend/alembic/versions/20260606_add_professor_tags.py backend/app/schemas/professor.py backend/app/services/professor_management.py backend/test/test_professor_management.py
git commit -m "feat(backend): 添加导师标签数据模型"
```

## 任务 2：后端标签 API 和教授接口接入

**文件：**
- 创建：`backend/test/test_professor_tags.py`
- 修改：`backend/app/api/professors.py`

- [ ] **步骤 1：编写失败的 API 测试**

创建 `backend/test/test_professor_tags.py`，测试内容包括：

```python
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Professor, ProfessorTag
from test.schema_database import isolated_schema_database


class ProfessorTagsApiTests(unittest.TestCase):
    def test_default_tags_are_listed(self) -> None:
        with isolated_schema_database():
            response = TestClient(app).get("/api/professors/tags")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["name"] for item in response.json()],
            ["已退休", "低意愿", "羊导", "高强度", "高意愿"],
        )

    def test_create_professor_with_multiple_tags_and_list_them(self) -> None:
        with isolated_schema_database():
            client = TestClient(app)
            tags = client.get("/api/professors/tags").json()
            selected_ids = [tags[0]["id"], tags[1]["id"]]
            response = client.post(
                "/api/professors",
                json={
                    "name": "张明远",
                    "email": "zhang@example.edu",
                    "tag_ids": selected_ids,
                },
            )
            dashboard = client.get("/api/professors").json()

        self.assertEqual(response.status_code, 201)
        self.assertEqual([tag["id"] for tag in response.json()["tags"]], selected_ids)
        self.assertEqual([tag["id"] for tag in dashboard[0]["tags"]], selected_ids)

    def test_delete_tag_removes_professor_links(self) -> None:
        with isolated_schema_database() as session:
            client = TestClient(app)
            tag = client.get("/api/professors/tags").json()[0]
            created = client.post(
                "/api/professors",
                json={
                    "name": "李伟",
                    "email": "li@example.edu",
                    "tag_ids": [tag["id"]],
                },
            ).json()
            delete_response = client.delete(f"/api/professors/tags/{tag['id']}")
            refreshed = client.get(f"/api/professors/{created['id']}").json()

        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(refreshed["tags"], [])
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_professor_tags
```

预期：FAIL，失败原因是标签 API 路由不存在或 DTO 未返回 tags。

- [ ] **步骤 3：实现标签 API 和序列化**

在 `backend/app/api/professors.py` 中：

- import `selectinload`、`delete`、`ProfessorTag`、`ProfessorTagRead`、`ProfessorTagPayload`。
- 列表查询增加 `selectinload(Professor.tags)`。
- 新增 `_serialize_tag(tag: ProfessorTag) -> ProfessorTagRead`。
- 新增 `_serialize_professor_tags(professor: Professor) -> list[ProfessorTagRead]`。
- `_serialize_management_professor` 和 dashboard 返回 DTO 填充 `tags`。
- `get_professor` 改为返回 `ProfessorRead` 并加载 tags。
- 新增 `GET /tags`、`POST /tags`、`DELETE /tags/{tag_id}`。
- 新增 `_load_tags_by_ids(session, tag_ids)`，若重复、缺失或不存在则抛 `HTTPException(400, "标签不存在")`。
- create/update professor 时用 `professor.tags = await _load_tags_by_ids(session, payload.tag_ids)`。

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令。

预期：PASS。

- [ ] **步骤 5：提交任务 2**

```powershell
git add backend/test/test_professor_tags.py backend/app/api/professors.py
git commit -m "feat(backend): 接入导师标签接口"
```

## 任务 3：前端类型、API、标签展示组件和筛选逻辑

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/lib/api/professorsApi.ts`
- 创建：`frontend/src/components/molecules/ProfessorTagChips.tsx`
- 创建：`frontend/src/components/molecules/ProfessorTagChips.test.tsx`
- 修改：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts`
- 修改：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts`
- 修改：`frontend/src/features/professor-management/client/filterManagementProfessors.ts`
- 修改：`frontend/src/features/professor-management/client/filterManagementProfessors.test.ts`

- [ ] **步骤 1：编写失败的前端测试**

在 `ProfessorTagChips.test.tsx` 覆盖无标签、前 3 个和 `+N`：

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProfessorTagChips } from "./ProfessorTagChips";

const tag = (id: number, name: string) => ({
  id,
  name,
  text_color: "#166534",
  background_color: "#dcfce7",
});

describe("ProfessorTagChips", () => {
  it("shows no tag state", () => {
    render(<ProfessorTagChips tags={[]} />);
    expect(screen.getByText("暂无标签")).toBeInTheDocument();
  });

  it("limits visible tags and shows overflow count", () => {
    render(
      <ProfessorTagChips
        maxVisible={2}
        tags={[tag(1, "高意愿"), tag(2, "高强度"), tag(3, "羊导")]}
      />,
    );
    expect(screen.getByText("高意愿")).toBeInTheDocument();
    expect(screen.getByText("高强度")).toBeInTheDocument();
    expect(screen.queryByText("羊导")).not.toBeInTheDocument();
    expect(screen.getByText("+1")).toBeInTheDocument();
  });
});
```

在首页筛选测试中新增标签关键词和标签筛选测试，在管理页筛选测试中新增同类测试。

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd frontend
npm.cmd test -- src/components/molecules/ProfessorTagChips.test.tsx src/features/home-dashboard/client/filterDashboardProfessors.test.ts src/features/professor-management/client/filterManagementProfessors.test.ts
```

预期：FAIL，失败原因是组件不存在、DTO 无 tags 或筛选字段不存在。

- [ ] **步骤 3：实现类型、API、展示组件和筛选逻辑**

在 `frontend/src/types/index.ts` 新增：

```typescript
export interface ProfessorTagDTO {
  id: number;
  name: string;
  text_color: string;
  background_color: string;
}

export interface ProfessorTagPayloadDTO {
  name: string;
  text_color: string;
  background_color: string;
}
```

给 `ProfessorDashboardItemDTO`、`ProfessorDTO`、`ProfessorManagementItemDTO` 增加 `tags: ProfessorTagDTO[]`，给 `ProfessorUpsertPayloadDTO` 增加 `tag_ids: number[]`。

在 `professorsApi.ts` 新增 `listProfessorTags`、`createProfessorTag`、`deleteProfessorTag`。

实现 `ProfessorTagChips`：无标签显示“暂无标签”，默认 `maxVisible=3`，管理页可传 `maxVisible=2`，颜色使用 inline style。

在两个筛选模块中：

- 增加 `keywordSearchScopes`，选项包含 `tag`。
- 增加 `tagIds: number[]`，默认 `[]` 表示全部。
- 增加虚拟常量 `NO_TAG_FILTER_VALUE = "__no_tag__"`。
- keyword 匹配按 scopes 取字段，标签字段匹配 `professor.tags.map(tag => tag.name)`。
- 标签筛选空数组表示全部；非空时多标签导师命中任一选中 tag id 保留，`NO_TAG_FILTER_VALUE` 命中无标签导师。
- filter options 增加 `tags`，来自标签候选或教授标签集合，按中文排序。

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令。

预期：PASS。

- [ ] **步骤 5：提交任务 3**

```powershell
git add frontend/src/types/index.ts frontend/src/lib/api/professorsApi.ts frontend/src/components/molecules/ProfessorTagChips.tsx frontend/src/components/molecules/ProfessorTagChips.test.tsx frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts frontend/src/features/professor-management/client/filterManagementProfessors.ts frontend/src/features/professor-management/client/filterManagementProfessors.test.ts
git commit -m "feat(frontend): 添加导师标签展示和筛选逻辑"
```

## 任务 4：导师编辑弹窗标签选择

**文件：**
- 创建：`frontend/src/components/molecules/ProfessorTagSelector.tsx`
- 创建：`frontend/src/components/molecules/ProfessorTagSelector.test.tsx`
- 修改：`frontend/src/pages/ProfessorsPage.tsx`

- [ ] **步骤 1：编写失败的选择器测试**

创建测试：

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProfessorTagSelector } from "./ProfessorTagSelector";

const tags = [
  { id: 1, name: "高意愿", text_color: "#166534", background_color: "#dcfce7" },
  { id: 2, name: "羊导", text_color: "#7f1d1d", background_color: "#fee2e2" },
];

describe("ProfessorTagSelector", () => {
  it("toggles multiple tags", () => {
    const onChange = vi.fn();
    render(<ProfessorTagSelector tags={tags} selectedTagIds={[1]} onChange={onChange} onCreateTag={vi.fn()} onDeleteTag={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "选择标签 羊导" }));
    expect(onChange).toHaveBeenCalledWith([1, 2]);
  });

  it("submits custom tag with colors", () => {
    const onCreateTag = vi.fn();
    render(<ProfessorTagSelector tags={tags} selectedTagIds={[]} onChange={vi.fn()} onCreateTag={onCreateTag} onDeleteTag={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "+ 自定义标签" }));
    fireEvent.change(screen.getByLabelText("标签名"), { target: { value: "已联系" } });
    fireEvent.click(screen.getByRole("button", { name: "创建标签" }));
    expect(onCreateTag).toHaveBeenCalledWith({
      name: "已联系",
      text_color: "#166534",
      background_color: "#dcfce7",
    });
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd frontend
npm.cmd test -- src/components/molecules/ProfessorTagSelector.test.tsx
```

预期：FAIL，组件不存在。

- [ ] **步骤 3：实现选择器和管理页接入**

实现 `ProfessorTagSelector`：

- props：`tags`、`selectedTagIds`、`onChange`、`onCreateTag`、`onDeleteTag`、`disabled`。
- 默认无选中显示“暂无标签”。
- 真实标签按钮支持切换。
- `+ 自定义标签` 展开表单，默认颜色 `#166534` 和 `#dcfce7`。
- 删除按钮调用 `onDeleteTag(tag)`。

在 `ProfessorsPage.tsx`：

- 页面加载时调用 `listProfessorTags`。
- `ProfessorFormState` 增加 `tag_ids`。
- `emptyProfessorForm` 初始化空数组。
- `toProfessorForm` 从 `professor.tags` 取 ids。
- `toProfessorPayload` 传 `tag_ids`。
- 弹窗中插入 `ProfessorTagSelector`。
- `onCreateTag` 调用 API，成功后加入候选并自动选中。
- `onDeleteTag` 使用现有 confirm，成功后刷新候选和导师列表，并从当前 formState 移除 id。

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令。

预期：PASS。

- [ ] **步骤 5：提交任务 4**

```powershell
git add frontend/src/components/molecules/ProfessorTagSelector.tsx frontend/src/components/molecules/ProfessorTagSelector.test.tsx frontend/src/pages/ProfessorsPage.tsx
git commit -m "feat(frontend): 支持编辑导师标签"
```

## 任务 5：首页和管理页展示、筛选 UI 接入

**文件：**
- 修改：`frontend/src/components/molecules/DashboardProfessorRow.tsx`
- 修改：`frontend/src/components/molecules/ManagementProfessorRow.tsx`
- 修改：`frontend/src/pages/HomePage.tsx`
- 修改：`frontend/src/pages/ProfessorsPage.tsx`

- [ ] **步骤 1：编写失败的页面/行组件测试**

优先扩展已有页面或行组件测试，至少覆盖：

```tsx
render(<DashboardProfessorRow professor={{ ...professor, tags: [tag] }} ... />);
expect(screen.getByText("高意愿")).toBeInTheDocument();
```

和管理页行组件：

```tsx
render(<ManagementProfessorRow professor={{ ...professor, tags: [tag] }} ... />);
expect(screen.getByText("高意愿")).toBeInTheDocument();
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd frontend
npm.cmd test -- src/pages/DashboardPage.test.tsx src/pages/ProfessorsPage.test.tsx
```

预期：FAIL，标签未展示或筛选控件不存在。

- [ ] **步骤 3：实现页面接入**

在 `DashboardProfessorRow` 的 `ProfessorIdentityBlock` 下方或姓名区域接入 `ProfessorTagChips tags={professor.tags} maxVisible={3}`。

在 `ManagementProfessorRow` 姓名单元格第二行接入 `ProfessorTagChips tags={professor.tags} maxVisible={2}`。

在 `HomePage.tsx`：

- 加载标签候选。
- 关键词搜索区域接入已有搜索范围控件或新增简洁多选，包含“标签”。
- 高级筛选增加 `MultiSelectFilter label="标签"`，选项为真实标签名加“暂无标签”，内部映射到 tag id 和 `NO_TAG_FILTER_VALUE`。
- 清空高级筛选时清空 `tagIds`。

在 `ProfessorsPage.tsx`：

- 管理页工具条同样接入搜索范围和标签筛选。
- 删除标签后触发筛选状态 prune。

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令；如页面测试范围过大，至少运行新增/修改的行组件测试和两个筛选模块测试。

预期：PASS。

- [ ] **步骤 5：提交任务 5**

```powershell
git add frontend/src/components/molecules/DashboardProfessorRow.tsx frontend/src/components/molecules/ManagementProfessorRow.tsx frontend/src/pages/HomePage.tsx frontend/src/pages/ProfessorsPage.tsx
git commit -m "feat(frontend): 在导师列表接入标签"
```

## 任务 6：全量聚焦验证

**文件：**
- 检查：所有修改文件

- [ ] **步骤 1：运行后端聚焦测试**

```powershell
cd backend
uv run python -m unittest test.test_professor_tags test.test_professor_management
```

预期：全部 PASS。

- [ ] **步骤 2：运行前端聚焦测试**

```powershell
cd frontend
npm.cmd test -- src/components/molecules/ProfessorTagChips.test.tsx src/components/molecules/ProfessorTagSelector.test.tsx src/features/home-dashboard/client/filterDashboardProfessors.test.ts src/features/professor-management/client/filterManagementProfessors.test.ts
```

预期：全部 PASS。

- [ ] **步骤 3：运行静态验证**

```powershell
cd frontend
npm.cmd run lint
npm.cmd run build
```

预期：退出码为 0。

- [ ] **步骤 4：检查 diff**

```powershell
git status -sb
git diff --stat HEAD~5..HEAD
```

预期：只包含导师标签功能和计划文档相关改动。

- [ ] **步骤 5：提交计划文档最终状态**

如果执行过程中更新了计划复选框或修正计划：

```powershell
git add docs/superpowers/plans/2026-06-06-professor-tags.md
git commit -m "docs: 更新导师标签实现计划"
```
