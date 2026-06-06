# 导师标签交互与排序二期实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为导师标签补齐每位导师独立排序、折叠查看、管理页主显示标签、编辑页删除模式和拖拽排序。

**架构：** 后端在 `professor_tag_links` 上持久化 `sort_order`，创建/更新导师按 `tag_ids` 数组顺序保存并返回有序标签。前端将标签展示抽为可弹层展开的 `ProfessorTagChips`，编辑页选择器负责删除模式和已选标签排序，管理页通过置顶标签保存主显示标签。

**技术栈：** FastAPI、SQLAlchemy、Alembic、unittest、React、TypeScript、Vitest、Testing Library、Tailwind CSS。

---

## 文件职责

- `backend/app/models/professor.py`：给 `ProfessorTagLink` 增加 `sort_order`，让 `Professor.tags` 按关联排序。
- `backend/alembic/versions/20260606_add_professor_tag_sort_order.py`：迁移增加 `sort_order` 并初始化已有关联顺序。
- `backend/app/schemas/professor.py`：新增标签 usage DTO。
- `backend/app/api/professors.py`：保存标签顺序、返回有序标签、新增 usage 接口。
- `backend/test/test_professor_tags.py`：覆盖排序保存、排序更新和 usage 接口。
- `frontend/src/lib/api/professorsApi.ts`：新增 `getProfessorTagUsage` API。
- `frontend/src/types/index.ts`：新增 usage DTO。
- `frontend/src/components/molecules/ProfessorTagChips.tsx`：支持 `+N` 弹层、单标签模式、弹层内标签点击。
- `frontend/src/components/molecules/ProfessorTagChips.test.tsx`：覆盖弹层展开和点击标签。
- `frontend/src/components/molecules/DashboardProfessorRow.tsx`：把标签移动到姓名同一行。
- `frontend/src/components/molecules/DashboardProfessorRow.test.tsx`：覆盖标签与姓名同一行。
- `frontend/src/components/molecules/ManagementProfessorRow.tsx`：只展示主标签和 `+N`，支持置顶回调。
- `frontend/src/components/molecules/ManagementProfessorRow.test.tsx`：覆盖单标签展示和点击置顶。
- `frontend/src/components/molecules/ProfessorTagSelector.tsx`：放大编辑区、删除模式、已选标签拖拽排序。
- `frontend/src/components/molecules/ProfessorTagSelector.test.tsx`：覆盖删除模式和拖拽排序。
- `frontend/src/pages/ProfessorsPage.tsx`：接入 usage 删除确认、管理页置顶保存。

## 任务 1：后端标签排序和 usage API

**文件：**
- 修改：`backend/app/models/professor.py`
- 创建：`backend/alembic/versions/20260606_add_professor_tag_sort_order.py`
- 修改：`backend/app/schemas/professor.py`
- 修改：`backend/app/api/professors.py`
- 修改：`backend/test/test_professor_tags.py`

- [ ] **步骤 1：编写失败的后端测试**

在 `backend/test/test_professor_tags.py` 添加：

```python
    def test_professor_tags_keep_payload_order(self) -> None:
        with isolated_schema_database():
            client = TestClient(app)
            tags = client.get("/api/professors/tags").json()
            selected_ids = [tags[2]["id"], tags[0]["id"], tags[1]["id"]]
            created = client.post(
                "/api/professors",
                json={
                    "name": "排序导师",
                    "email": "ordered@example.edu",
                    "tag_ids": selected_ids,
                },
            ).json()
            updated = client.patch(
                f"/api/professors/{created['id']}",
                json={
                    "name": "排序导师",
                    "email": "ordered@example.edu",
                    "tag_ids": [selected_ids[1], selected_ids[2], selected_ids[0]],
                },
            ).json()

        self.assertEqual([tag["id"] for tag in created["tags"]], selected_ids)
        self.assertEqual(
            [tag["id"] for tag in updated["tags"]],
            [selected_ids[1], selected_ids[2], selected_ids[0]],
        )

    def test_tag_usage_lists_professors_using_tag(self) -> None:
        with isolated_schema_database():
            client = TestClient(app)
            tag = client.get("/api/professors/tags").json()[0]
            client.post(
                "/api/professors",
                json={
                    "name": "使用标签导师",
                    "email": "usage@example.edu",
                    "university": "示例大学",
                    "school": "计算机学院",
                    "tag_ids": [tag["id"]],
                },
            )
            usage = client.get(f"/api/professors/tags/{tag['id']}/usage").json()

        self.assertEqual(usage["tag"]["id"], tag["id"])
        self.assertEqual([item["name"] for item in usage["professors"]], ["使用标签导师"])
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest test.test_professor_tags
```

预期：FAIL，排序测试返回名称排序而非 payload 顺序，usage 接口 404。

- [ ] **步骤 3：实现最少后端代码**

实现：

- `ProfessorTagLink.sort_order: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))`
- `Professor.tags` 的 `order_by` 改为 `ProfessorTagLink.sort_order.asc(), ProfessorTag.name.asc()`。
- 新迁移增加 `sort_order` 字段。
- 新增 `ProfessorTagUsageProfessorRead` 和 `ProfessorTagUsageRead`。
- 新增 `GET /api/professors/tags/{tag_id}/usage`。
- 新增 `_sync_professor_tags(session, professor, tag_ids)`，按 tag_ids 顺序删除旧 link 并插入新 link。
- create/update professor 使用 `_sync_professor_tags`，提交后重新查询带 tags 的 professor 再序列化。

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令。预期：PASS。

- [ ] **步骤 5：提交任务 1**

```powershell
git add backend/app/models/professor.py backend/alembic/versions/20260606_add_professor_tag_sort_order.py backend/app/schemas/professor.py backend/app/api/professors.py backend/test/test_professor_tags.py
git commit -m "feat(backend): 支持导师标签排序和使用查询"
```

## 任务 2：标签折叠弹层和首页同排展示

**文件：**
- 修改：`frontend/src/components/molecules/ProfessorTagChips.tsx`
- 修改：`frontend/src/components/molecules/ProfessorTagChips.test.tsx`
- 修改：`frontend/src/components/molecules/DashboardProfessorRow.tsx`
- 修改：`frontend/src/components/molecules/DashboardProfessorRow.test.tsx`

- [ ] **步骤 1：编写失败的前端测试**

扩展 `ProfessorTagChips.test.tsx`：

```tsx
it("shows hidden tags in a popover", () => {
  render(
    <ProfessorTagChips
      maxVisible={1}
      tags={[tag(1, "高意愿"), tag(2, "羊导"), tag(3, "高强度")]}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "查看全部标签，剩余 2 个" }));
  expect(screen.getByRole("dialog", { name: "全部标签" })).toBeInTheDocument();
  expect(screen.getByText("羊导")).toBeInTheDocument();
  expect(screen.getByText("高强度")).toBeInTheDocument();
});
```

扩展 `DashboardProfessorRow.test.tsx`，断言姓名同一行容器内有标签：

```tsx
expect(screen.getByTestId("dashboard-professor-name-line")).toHaveTextContent("张明远");
expect(screen.getByTestId("dashboard-professor-name-line")).toHaveTextContent("高意愿");
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd frontend
npm.cmd test -- src/components/molecules/ProfessorTagChips.test.tsx src/components/molecules/DashboardProfessorRow.test.tsx
```

预期：FAIL，`+N` 不是按钮且姓名同排 test id 不存在。

- [ ] **步骤 3：实现组件和首页行**

实现：

- `ProfessorTagChips` 的 `+N` 改为 button。
- 点击 `+N` 显示绝对定位弹层，弹层 `role="dialog"`、`aria-label="全部标签"`。
- 鼠标进入 `+N` 打开，鼠标离开组件容器关闭。
- 支持 `maxVisible`，首页传 2。
- `DashboardProfessorRow` 不再把标签放在 `ProfessorIdentityBlock` 后面，而是在姓名同一行单独渲染姓名、标签、学校信息和研究方向。

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令。预期：PASS。

- [ ] **步骤 5：提交任务 2**

```powershell
git add frontend/src/components/molecules/ProfessorTagChips.tsx frontend/src/components/molecules/ProfessorTagChips.test.tsx frontend/src/components/molecules/DashboardProfessorRow.tsx frontend/src/components/molecules/DashboardProfessorRow.test.tsx
git commit -m "feat(frontend): 支持标签折叠弹层"
```

## 任务 3：导师管理页主标签展示和置顶

**文件：**
- 修改：`frontend/src/components/molecules/ProfessorTagChips.tsx`
- 修改：`frontend/src/components/molecules/ProfessorTagChips.test.tsx`
- 修改：`frontend/src/components/molecules/ManagementProfessorRow.tsx`
- 修改：`frontend/src/components/molecules/ManagementProfessorRow.test.tsx`
- 修改：`frontend/src/pages/ProfessorsPage.tsx`

- [ ] **步骤 1：编写失败的测试**

在 `ProfessorTagChips.test.tsx` 添加点击回调测试：

```tsx
it("calls onTagClick from popover", () => {
  const onTagClick = vi.fn();
  render(
    <ProfessorTagChips
      maxVisible={1}
      tags={[tag(1, "高意愿"), tag(2, "羊导")]}
      onTagClick={onTagClick}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "查看全部标签，剩余 1 个" }));
  fireEvent.click(screen.getByRole("button", { name: "选择标签 羊导" }));
  expect(onTagClick).toHaveBeenCalledWith(2);
});
```

在 `ManagementProfessorRow.test.tsx` 添加：

```tsx
expect(screen.getByText("高意愿")).toBeInTheDocument();
expect(screen.queryByText("羊导")).not.toBeInTheDocument();
expect(screen.getByRole("button", { name: "查看全部标签，剩余 1 个" })).toBeInTheDocument();
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd frontend
npm.cmd test -- src/components/molecules/ProfessorTagChips.test.tsx src/components/molecules/ManagementProfessorRow.test.tsx
```

预期：FAIL，`onTagClick` 未实现，管理页仍显示两个标签。

- [ ] **步骤 3：实现管理页置顶**

实现：

- `ProfessorTagChips` 增加 `onTagClick?: (tagId: number) => void`。
- 有 `onTagClick` 时弹层标签渲染为 button。
- `ManagementProfessorRow` 增加 `onPromoteTag: (tagId: number) => void`，传给 chips，`maxVisible={1}`。
- `ProfessorsPage` 实现 `handlePromoteProfessorTag(professor, tagId)`：把 tagId 移到该导师 tags 第一位，调用 `updateProfessor` 保存。

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令。预期：PASS。

- [ ] **步骤 5：提交任务 3**

```powershell
git add frontend/src/components/molecules/ProfessorTagChips.tsx frontend/src/components/molecules/ProfessorTagChips.test.tsx frontend/src/components/molecules/ManagementProfessorRow.tsx frontend/src/components/molecules/ManagementProfessorRow.test.tsx frontend/src/pages/ProfessorsPage.tsx
git commit -m "feat(frontend): 支持管理页标签置顶"
```

## 任务 4：编辑页删除模式和拖拽排序

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/lib/api/professorsApi.ts`
- 修改：`frontend/src/components/molecules/ProfessorTagSelector.tsx`
- 修改：`frontend/src/components/molecules/ProfessorTagSelector.test.tsx`
- 修改：`frontend/src/pages/ProfessorsPage.tsx`

- [ ] **步骤 1：编写失败的测试**

扩展 `ProfessorTagSelector.test.tsx`：

```tsx
it("shows delete buttons only in delete mode", () => {
  render(<ProfessorTagSelector tags={tags} selectedTagIds={[]} onChange={vi.fn()} onCreateTag={vi.fn()} onDeleteTag={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "删除标签 高意愿" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "删除标签" }));
  expect(screen.getByRole("button", { name: "删除标签 高意愿" })).toBeInTheDocument();
});

it("reorders selected tags with move buttons", () => {
  const onChange = vi.fn();
  render(<ProfessorTagSelector tags={tags} selectedTagIds={[1, 2]} onChange={onChange} onCreateTag={vi.fn()} onDeleteTag={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "后移标签 高意愿" }));
  expect(onChange).toHaveBeenCalledWith([2, 1]);
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd frontend
npm.cmd test -- src/components/molecules/ProfessorTagSelector.test.tsx
```

预期：FAIL，删除模式和排序按钮不存在。

- [ ] **步骤 3：实现编辑页交互**

实现：

- `ProfessorTagSelector` 顶部按钮区增加“删除标签”按钮。
- 删除模式状态 `deleting` 控制垃圾桶显示。
- 已选标签区域展示较大胶囊，并提供“前移标签 X”“后移标签 X”按钮作为可测试、可键盘操作的排序入口；同时给胶囊设置 `draggable`，支持拖拽排序。
- 候选标签平时不显示垃圾桶，删除模式下垃圾桶从右侧出现。
- 新增 `ProfessorTagUsageDTO`、`getProfessorTagUsage`。
- `ProfessorsPage` 删除前调用 usage，按是否有导师使用生成确认描述。

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令。预期：PASS。

- [ ] **步骤 5：提交任务 4**

```powershell
git add frontend/src/types/index.ts frontend/src/lib/api/professorsApi.ts frontend/src/components/molecules/ProfessorTagSelector.tsx frontend/src/components/molecules/ProfessorTagSelector.test.tsx frontend/src/pages/ProfessorsPage.tsx
git commit -m "feat(frontend): 优化标签编辑交互"
```

## 任务 5：全量聚焦验证和计划提交

**文件：**
- 创建：`docs/superpowers/plans/2026-06-06-professor-tags-interactions.md`

- [ ] **步骤 1：运行后端测试**

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest test.test_professor_tags test.test_professor_management
```

预期：OK。

- [ ] **步骤 2：运行前端聚焦测试**

```powershell
cd frontend
npm.cmd test -- src/components/molecules/ProfessorTagChips.test.tsx src/components/molecules/ProfessorTagSelector.test.tsx src/components/molecules/DashboardProfessorRow.test.tsx src/components/molecules/ManagementProfessorRow.test.tsx src/features/home-dashboard/client/filterDashboardProfessors.test.ts src/features/professor-management/client/filterManagementProfessors.test.ts
```

预期：全部 PASS。

- [ ] **步骤 3：运行静态验证**

```powershell
cd frontend
npm.cmd run lint
npm.cmd run build
```

预期：退出码 0。

- [ ] **步骤 4：提交计划文档**

```powershell
git add docs/superpowers/plans/2026-06-06-professor-tags-interactions.md
git commit -m "docs: 添加导师标签交互排序实现计划"
```
