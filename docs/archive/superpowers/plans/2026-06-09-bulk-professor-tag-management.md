# 批量导师标签管理实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在首页和导师管理页为已选导师提供批量追加、移除、覆盖标签能力。

**架构：** 后端新增 `POST /api/professors/bulk-tags`，一次事务处理所选导师标签集合并返回更新后的导师数据。前端新增共享 `BulkProfessorTagDialog` 和 API 方法，两页在 sticky 批量操作框中打开同一弹窗，并用响应更新当前列表标签。

**技术栈：** FastAPI、SQLAlchemy AsyncSession、Pydantic、React、TypeScript、Vitest、Testing Library、unittest。

---

## 文件结构

- 修改 `backend/app/schemas/professor.py`：新增批量标签请求类型、模式 Literal、轻量更新结果类型。
- 修改 `backend/app/api/professors.py`：新增 `/bulk-tags` 路由和批量标签同步辅助函数，复用 `_load_tags_by_ids`、`_serialize_management_professor`。
- 修改 `backend/test/test_professor_tags.py`：增加批量追加、移除、覆盖、清空和错误回滚测试。
- 修改 `frontend/src/types/index.ts`：新增批量标签模式、请求 DTO、更新结果 DTO。
- 修改 `frontend/src/lib/api/professorsApi.ts`：新增 `bulkUpdateProfessorTags` API。
- 创建 `frontend/src/components/molecules/BulkProfessorTagDialog.tsx`：批量模式切换、标签选择、新建标签、删除标签定义、保存。
- 创建 `frontend/src/components/molecules/BulkProfessorTagDialog.test.tsx`：覆盖默认模式、提交 payload、追加/移除空标签禁用、覆盖空标签允许、删除标签入口。
- 修改 `frontend/src/pages/HomePage.tsx`：新增批量标签弹窗状态、保存逻辑、sticky 按钮和列表标签刷新。
- 修改 `frontend/src/pages/ProfessorsPage.tsx`：新增批量标签弹窗状态、保存逻辑、sticky 按钮和列表标签刷新。
- 修改 `frontend/src/pages/SelectionControls.test.tsx`：覆盖两页入口和成功更新行为。

## 任务 1：后端批量标签接口测试

**文件：**
- 修改：`backend/test/test_professor_tags.py`

- [ ] **步骤 1：编写失败测试**

在 `ProfessorTagsApiTest` 中追加测试方法：

```python
    def test_bulk_add_professor_tags_preserves_existing_tags(self) -> None:
        tags = self.client.get("/api/professors/tags").json()
        first_tag_id = tags[0]["id"]
        second_tag_id = tags[1]["id"]
        first = self.client.post(
            "/api/professors",
            json={
                "name": "批量追加一",
                "email": "bulk-add-1@example.edu",
                "tag_ids": [first_tag_id],
            },
        ).json()
        second = self.client.post(
            "/api/professors",
            json={
                "name": "批量追加二",
                "email": "bulk-add-2@example.edu",
                "tag_ids": [],
            },
        ).json()

        response = self.client.post(
            "/api/professors/bulk-tags",
            json={
                "professor_ids": [first["id"], second["id"]],
                "mode": "add",
                "tag_ids": [second_tag_id],
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["affected_count"], 2)
        tags_by_id = {
            item["id"]: [tag["id"] for tag in item["tags"]]
            for item in payload["professors"]
        }
        self.assertEqual(tags_by_id[first["id"]], [first_tag_id, second_tag_id])
        self.assertEqual(tags_by_id[second["id"]], [second_tag_id])

    def test_bulk_remove_professor_tags_preserves_other_tags(self) -> None:
        tags = self.client.get("/api/professors/tags").json()
        first_tag_id = tags[0]["id"]
        second_tag_id = tags[1]["id"]
        professor = self.client.post(
            "/api/professors",
            json={
                "name": "批量移除",
                "email": "bulk-remove@example.edu",
                "tag_ids": [first_tag_id, second_tag_id],
            },
        ).json()

        response = self.client.post(
            "/api/professors/bulk-tags",
            json={
                "professor_ids": [professor["id"]],
                "mode": "remove",
                "tag_ids": [first_tag_id],
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(
            [tag["id"] for tag in response.json()["professors"][0]["tags"]],
            [second_tag_id],
        )

    def test_bulk_replace_professor_tags_allows_empty_tags(self) -> None:
        tags = self.client.get("/api/professors/tags").json()
        tag_id = tags[0]["id"]
        professor = self.client.post(
            "/api/professors",
            json={
                "name": "批量覆盖",
                "email": "bulk-replace@example.edu",
                "tag_ids": [tag_id],
            },
        ).json()

        response = self.client.post(
            "/api/professors/bulk-tags",
            json={
                "professor_ids": [professor["id"]],
                "mode": "replace",
                "tag_ids": [],
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["professors"][0]["tags"], [])

    def test_bulk_tags_rejects_empty_add_without_partial_update(self) -> None:
        tags = self.client.get("/api/professors/tags").json()
        tag_id = tags[0]["id"]
        professor = self.client.post(
            "/api/professors",
            json={
                "name": "批量错误",
                "email": "bulk-error@example.edu",
                "tag_ids": [tag_id],
            },
        ).json()

        response = self.client.post(
            "/api/professors/bulk-tags",
            json={
                "professor_ids": [professor["id"]],
                "mode": "add",
                "tag_ids": [],
            },
        )
        refreshed = self.client.get(f"/api/professors/{professor['id']}").json()

        self.assertEqual(response.status_code, 400, msg=response.text)
        self.assertEqual(response.json()["detail"], "请选择要追加或移除的标签")
        self.assertEqual([tag["id"] for tag in refreshed["tags"]], [tag_id])

    def test_bulk_tags_rejects_missing_professor_without_partial_update(self) -> None:
        tags = self.client.get("/api/professors/tags").json()
        first_tag_id = tags[0]["id"]
        second_tag_id = tags[1]["id"]
        professor = self.client.post(
            "/api/professors",
            json={
                "name": "批量缺失导师",
                "email": "bulk-missing-professor@example.edu",
                "tag_ids": [first_tag_id],
            },
        ).json()

        response = self.client.post(
            "/api/professors/bulk-tags",
            json={
                "professor_ids": [professor["id"], 999999],
                "mode": "replace",
                "tag_ids": [second_tag_id],
            },
        )
        refreshed = self.client.get(f"/api/professors/{professor['id']}").json()

        self.assertEqual(response.status_code, 404, msg=response.text)
        self.assertEqual(response.json()["detail"], "导师不存在")
        self.assertEqual([tag["id"] for tag in refreshed["tags"]], [first_tag_id])
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_professor_tags.ProfessorTagsApiTest.test_bulk_add_professor_tags_preserves_existing_tags test.test_professor_tags.ProfessorTagsApiTest.test_bulk_remove_professor_tags_preserves_other_tags test.test_professor_tags.ProfessorTagsApiTest.test_bulk_replace_professor_tags_allows_empty_tags test.test_professor_tags.ProfessorTagsApiTest.test_bulk_tags_rejects_empty_add_without_partial_update test.test_professor_tags.ProfessorTagsApiTest.test_bulk_tags_rejects_missing_professor_without_partial_update
```

预期：FAIL，`/api/professors/bulk-tags` 返回 404 或 schema 未定义。

## 任务 2：后端接口实现

**文件：**
- 修改：`backend/app/schemas/professor.py`
- 修改：`backend/app/api/professors.py`

- [ ] **步骤 1：新增 schema**

在 `ProfessorBulkArchivePayload` 后添加：

```python
ProfessorBulkTagMode = Literal["add", "remove", "replace"]


class ProfessorBulkTagsPayload(BaseModel):
    professor_ids: list[int]
    mode: ProfessorBulkTagMode
    tag_ids: list[int]


class ProfessorBulkTagsResult(ApiSchema):
    ok: bool
    affected_count: int
    professors: list[ProfessorManagementItemRead]
    message: str
```

并在文件顶部补充：

```python
from typing import Literal
```

- [ ] **步骤 2：导入 schema 和新增路由**

在 `backend/app/api/professors.py` 的 schema import 中加入：

```python
ProfessorBulkTagsPayload,
ProfessorBulkTagsResult,
```

在 `bulk_archive_professors` 后、`/{professor_id}/restore` 前添加：

```python
@router.post("/bulk-tags", response_model=ProfessorBulkTagsResult)
async def bulk_update_professor_tags(
    payload: ProfessorBulkTagsPayload,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorBulkTagsResult:
    if not payload.professor_ids:
        raise HTTPException(status_code=400, detail="请至少选择一位导师")
    if payload.mode in {"add", "remove"} and not payload.tag_ids:
        raise HTTPException(status_code=400, detail="请选择要追加或移除的标签")

    requested_professor_ids = list(dict.fromkeys(payload.professor_ids))
    professors = list(
        (
            await session.execute(
                select(Professor)
                .options(selectinload(Professor.tags))
                .where(Professor.id.in_(requested_professor_ids)),
            )
        ).scalars()
    )
    professors_by_id = {professor.id: professor for professor in professors}
    missing_professor_ids = [
        professor_id
        for professor_id in requested_professor_ids
        if professor_id not in professors_by_id
    ]
    if missing_professor_ids:
        raise HTTPException(status_code=404, detail="导师不存在")

    tags = await _load_tags_by_ids(session, payload.tag_ids)
    tag_ids = [tag.id for tag in tags]
    now = utc_now()
    for professor_id in requested_professor_ids:
        professor = professors_by_id[professor_id]
        current_tag_ids = [tag.id for tag in professor.tags]
        if payload.mode == "add":
            next_tag_ids = current_tag_ids + [
                tag_id for tag_id in tag_ids if tag_id not in current_tag_ids
            ]
        elif payload.mode == "remove":
            remove_tag_ids = set(tag_ids)
            next_tag_ids = [
                tag_id for tag_id in current_tag_ids if tag_id not in remove_tag_ids
            ]
        else:
            next_tag_ids = tag_ids
        await _sync_professor_tags(session, professor, next_tag_ids)
        professor.updated_at = now

    await session.flush()
    refreshed_professors = list(
        (
            await session.execute(
                select(Professor)
                .options(selectinload(Professor.tags))
                .where(Professor.id.in_(requested_professor_ids)),
            )
        ).scalars()
    )
    refreshed_by_id = {
        professor.id: professor for professor in refreshed_professors
    }
    ordered_professors = [
        refreshed_by_id[professor_id]
        for professor_id in requested_professor_ids
    ]

    await record_operation_log(
        session,
        category="user_action",
        event_name="professor.bulk_tags_updated",
        entity_type="professor",
        metadata={
            "requested_count": len(requested_professor_ids),
            "affected_count": len(ordered_professors),
            "ids": requested_professor_ids,
            "mode": payload.mode,
            "tag_ids": tag_ids,
        },
    )
    await session.commit()
    return ProfessorBulkTagsResult(
        ok=True,
        affected_count=len(ordered_professors),
        professors=[
            _serialize_management_professor(professor)
            for professor in ordered_professors
        ],
        message=f"已更新 {len(ordered_professors)} 位导师的标签",
    )
```

- [ ] **步骤 3：运行后端定向测试**

运行任务 1 中的 unittest 命令。

预期：PASS。

- [ ] **步骤 4：Commit 后端变更**

```powershell
git add backend/app/schemas/professor.py backend/app/api/professors.py backend/test/test_professor_tags.py
git commit -m "feat(backend): 支持批量更新导师标签"
```

## 任务 3：前端 API 和批量弹窗测试

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/lib/api/professorsApi.ts`
- 创建：`frontend/src/components/molecules/BulkProfessorTagDialog.test.tsx`

- [ ] **步骤 1：新增类型和 API**

在 `frontend/src/types/index.ts` 添加：

```ts
export type ProfessorBulkTagModeDTO = "add" | "remove" | "replace";

export type ProfessorBulkTagsPayloadDTO = {
  professor_ids: number[];
  mode: ProfessorBulkTagModeDTO;
  tag_ids: number[];
};

export type ProfessorBulkTagsResultDTO = {
  ok: boolean;
  affected_count: number;
  professors: ProfessorManagementItemDTO[];
  message: string;
};
```

在 `frontend/src/lib/api/professorsApi.ts` 的 type import 中加入：

```ts
ProfessorBulkTagsPayloadDTO,
ProfessorBulkTagsResultDTO,
```

在 `bulkArchiveProfessors` 后添加：

```ts
export const bulkUpdateProfessorTags = (payload: ProfessorBulkTagsPayloadDTO) =>
  apiFetch<ProfessorBulkTagsResultDTO>('/api/professors/bulk-tags', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
```

- [ ] **步骤 2：编写弹窗失败测试**

创建 `frontend/src/components/molecules/BulkProfessorTagDialog.test.tsx`：

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { BulkProfessorTagDialog } from "./BulkProfessorTagDialog";
import type { ProfessorTagDTO } from "@/types";

const tags: ProfessorTagDTO[] = [
  { id: 1, name: "高意愿", text_color: "#166534", background_color: "#dcfce7" },
  { id: 2, name: "已联系", text_color: "#1d4ed8", background_color: "#dbeafe" },
];

const renderDialog = (onSave = vi.fn()) =>
  render(
    <BulkProfessorTagDialog
      open
      selectedCount={3}
      tags={tags}
      saving={false}
      creating={false}
      onCreateTag={vi.fn()}
      onSave={onSave}
      onClose={vi.fn()}
    />,
  );

describe("BulkProfessorTagDialog", () => {
  it("defaults to add mode and submits selected tags", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    renderDialog(onSave);

    expect(screen.getByRole("button", { name: "追加标签" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await user.click(screen.getByRole("button", { name: "选择标签 高意愿" }));
    await user.click(screen.getByRole("button", { name: "追加标签" }));

    expect(onSave).toHaveBeenCalledWith({ mode: "add", tagIds: [1] });
  });

  it("submits remove mode", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    renderDialog(onSave);

    await user.click(screen.getByRole("button", { name: "移除标签" }));
    await user.click(screen.getByRole("button", { name: "选择标签 已联系" }));
    await user.click(screen.getByRole("button", { name: "移除标签" }));

    expect(onSave).toHaveBeenCalledWith({ mode: "remove", tagIds: [2] });
  });

  it("allows replace mode with empty tags", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    renderDialog(onSave);

    await user.click(screen.getByRole("button", { name: "覆盖标签" }));
    await user.click(screen.getByRole("button", { name: "覆盖标签" }));

    expect(onSave).toHaveBeenCalledWith({ mode: "replace", tagIds: [] });
  });

  it("disables add and remove save without tags", async () => {
    const user = userEvent.setup();
    renderDialog();

    expect(screen.getByRole("button", { name: "追加标签" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "移除标签" }));
    expect(screen.getByRole("button", { name: "移除标签" })).toBeDisabled();
  });
});
```

- [ ] **步骤 3：运行测试验证失败**

运行：

```powershell
cd frontend
npm.cmd run test -- BulkProfessorTagDialog.test.tsx
```

预期：FAIL，组件不存在或 API 类型不存在。

## 任务 4：实现批量弹窗

**文件：**
- 创建：`frontend/src/components/molecules/BulkProfessorTagDialog.tsx`

- [ ] **步骤 1：实现组件**

创建文件：

```tsx
import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { Check, Loader2, Plus, Tags } from "lucide-react";
import type {
  ProfessorBulkTagModeDTO,
  ProfessorTagDTO,
  ProfessorTagPayloadDTO,
} from "@/types";

type BulkProfessorTagDialogProps = {
  open: boolean;
  selectedCount: number;
  tags: ProfessorTagDTO[];
  saving?: boolean;
  creating?: boolean;
  onSave: (payload: { mode: ProfessorBulkTagModeDTO; tagIds: number[] }) => void;
  onCreateTag: (payload: ProfessorTagPayloadDTO) => Promise<ProfessorTagDTO | null>;
  onClose: () => void;
};

const DEFAULT_TEXT_COLOR = "#166534";
const DEFAULT_BACKGROUND_COLOR = "#dcfce7";

const modeLabels: Record<ProfessorBulkTagModeDTO, string> = {
  add: "追加标签",
  remove: "移除标签",
  replace: "覆盖标签",
};

export const BulkProfessorTagDialog = ({
  open,
  selectedCount,
  tags,
  saving = false,
  creating = false,
  onSave,
  onCreateTag,
  onClose,
}: BulkProfessorTagDialogProps) => {
  const [mode, setMode] = useState<ProfessorBulkTagModeDTO>("add");
  const [selectedTagIds, setSelectedTagIds] = useState<number[]>([]);
  const [creatingCustomTag, setCreatingCustomTag] = useState(false);
  const [name, setName] = useState("");
  const [textColor, setTextColor] = useState(DEFAULT_TEXT_COLOR);
  const [backgroundColor, setBackgroundColor] = useState(DEFAULT_BACKGROUND_COLOR);
  const createInFlightRef = useRef(false);
  const busy = saving || creating;
  const saveDisabled = busy || (mode !== "replace" && selectedTagIds.length === 0);

  useEffect(() => {
    if (!open) {
      return;
    }
    setMode("add");
    setSelectedTagIds([]);
    setCreatingCustomTag(false);
    setName("");
    setTextColor(DEFAULT_TEXT_COLOR);
    setBackgroundColor(DEFAULT_BACKGROUND_COLOR);
  }, [open]);

  if (!open) {
    return null;
  }

  const toggleTag = (tagId: number) => {
    setSelectedTagIds((previous) =>
      previous.includes(tagId)
        ? previous.filter((item) => item !== tagId)
        : [...previous, tagId],
    );
  };

  const handleCreate = async () => {
    const trimmedName = name.trim();
    if (!trimmedName || busy || createInFlightRef.current) {
      return;
    }
    createInFlightRef.current = true;
    try {
      const createdTag = await onCreateTag({
        name: trimmedName,
        text_color: textColor,
        background_color: backgroundColor,
      });
      if (createdTag && !selectedTagIds.includes(createdTag.id)) {
        setSelectedTagIds((previous) => [...previous, createdTag.id]);
      }
      if (createdTag) {
        setName("");
        setTextColor(DEFAULT_TEXT_COLOR);
        setBackgroundColor(DEFAULT_BACKGROUND_COLOR);
        setCreatingCustomTag(false);
      }
    } finally {
      createInFlightRef.current = false;
    }
  };

  return (
    <div
      role="dialog"
      aria-label="批量修改导师标签"
      aria-modal="true"
      className="fixed inset-0 z-[80] flex items-center justify-center bg-stone-950/35 p-4 backdrop-blur-md"
      onClick={() => {
        if (!busy) {
          onClose();
        }
      }}
    >
      <div
        className="w-full max-w-lg rounded-[28px] border border-stone-200 bg-white p-5 shadow-[0_28px_72px_-32px_rgba(41,37,36,0.55)]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-stone-900">批量修改导师标签</h2>
            <p className="mt-1 text-sm text-stone-500">
              将应用到 {selectedCount} 位导师
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-stone-300 hover:text-stone-900 disabled:cursor-not-allowed disabled:opacity-60"
            aria-label="关闭批量标签修改"
          >
            ×
          </button>
        </div>

        <div className="mt-5 grid grid-cols-3 gap-2 rounded-2xl bg-stone-100 p-1">
          {(["add", "remove", "replace"] satisfies ProfessorBulkTagModeDTO[]).map(
            (item) => (
              <button
                key={item}
                type="button"
                aria-pressed={mode === item}
                onClick={() => setMode(item)}
                disabled={busy}
                className={clsx(
                  "inline-flex min-h-9 items-center justify-center rounded-xl px-3 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-60",
                  mode === item
                    ? "bg-white text-stone-900 shadow-sm"
                    : "text-stone-600 hover:text-stone-900",
                )}
              >
                {modeLabels[item]}
              </button>
            ),
          )}
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          {tags.map((tag) => {
            const selected = selectedTagIds.includes(tag.id);
            return (
              <button
                key={tag.id}
                type="button"
                aria-label={`选择标签 ${tag.name}`}
                aria-pressed={selected}
                disabled={busy}
                onClick={() => toggleTag(tag.id)}
                className={clsx(
                  "inline-flex min-h-9 items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-60",
                  selected
                    ? "border-primary/40 shadow-sm shadow-primary/10"
                    : "border-stone-200 hover:border-stone-300",
                )}
                style={{
                  backgroundColor: tag.background_color,
                  color: tag.text_color,
                }}
              >
                {selected ? <Check className="h-3.5 w-3.5" /> : null}
                {tag.name}
              </button>
            );
          })}
        </div>

        {tags.length === 0 ? (
          <div className="mt-5 rounded-2xl border border-stone-200 bg-stone-50 px-4 py-3 text-sm text-stone-500">
            暂无可选标签，可先新增一个标签。
          </div>
        ) : null}

        <div className="mt-5">
          <button
            type="button"
            onClick={() => setCreatingCustomTag((previous) => !previous)}
            disabled={busy}
            className="ui-btn-secondary px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Plus className="h-4 w-4" />
            新增标签
          </button>
        </div>

        {creatingCustomTag ? (
          <div className="mt-4 grid gap-3 rounded-2xl border border-stone-200 bg-stone-50 p-3 md:grid-cols-[minmax(0,1fr)_7rem_7rem]">
            <label className="block md:col-span-3">
              <div className="mb-1 text-xs font-medium text-stone-600">标签名</div>
              <input
                aria-label="新增标签名"
                value={name}
                onChange={(event) => setName(event.target.value)}
                className="w-full rounded-xl border border-stone-200 px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
                placeholder="例如：已联系"
              />
            </label>
            <label className="block">
              <div className="mb-1 text-xs font-medium text-stone-600">字体颜色</div>
              <input
                aria-label="新增标签字体颜色"
                type="color"
                value={textColor}
                onChange={(event) => setTextColor(event.target.value)}
                className="h-10 w-full rounded-xl border border-stone-200 bg-white px-2"
              />
            </label>
            <label className="block">
              <div className="mb-1 text-xs font-medium text-stone-600">背景颜色</div>
              <input
                aria-label="新增标签背景颜色"
                type="color"
                value={backgroundColor}
                onChange={(event) => setBackgroundColor(event.target.value)}
                className="h-10 w-full rounded-xl border border-stone-200 bg-white px-2"
              />
            </label>
            <button
              type="button"
              onClick={() => void handleCreate()}
              disabled={busy || !name.trim()}
              className="ui-btn-primary justify-center self-end disabled:cursor-not-allowed disabled:opacity-60"
            >
              {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              创建标签
            </button>
          </div>
        ) : null}

        <div className="mt-6 flex flex-wrap justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => onSave({ mode, tagIds: selectedTagIds })}
            disabled={saveDisabled}
            className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
          >
            {saving ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Tags className="h-4 w-4" />
            )}
            {modeLabels[mode]}
          </button>
        </div>
      </div>
    </div>
  );
};
```

- [ ] **步骤 2：运行弹窗测试**

运行：

```powershell
cd frontend
npm.cmd run test -- BulkProfessorTagDialog.test.tsx
```

预期：PASS。

- [ ] **步骤 3：Commit 前端组件变更**

```powershell
git add frontend/src/types/index.ts frontend/src/lib/api/professorsApi.ts frontend/src/components/molecules/BulkProfessorTagDialog.tsx frontend/src/components/molecules/BulkProfessorTagDialog.test.tsx
git commit -m "feat(frontend): 添加批量导师标签弹窗"
```

## 任务 5：首页和导师管理页接入

**文件：**
- 修改：`frontend/src/pages/HomePage.tsx`
- 修改：`frontend/src/pages/ProfessorsPage.tsx`
- 修改：`frontend/src/pages/SelectionControls.test.tsx`

- [ ] **步骤 1：编写页面失败测试**

在 `frontend/src/pages/SelectionControls.test.tsx` 的 mock API 区域加入 `bulkUpdateProfessorTags: vi.fn()`，并导入 mock。追加两个测试：

```tsx
  it("bulk updates tags from the home selection bar", async () => {
    const { bulkUpdateProfessorTags } = await import("@/lib/api/professorsApi");
    vi.mocked(bulkUpdateProfessorTags).mockResolvedValue({
      ok: true,
      affected_count: 1,
      message: "已更新 1 位导师的标签",
      professors: [
        {
          ...createManagementProfessor(11, "导师 11"),
          tags: [
            {
              id: 1,
              name: "高意愿",
              text_color: "#166534",
              background_color: "#dcfce7",
            },
          ],
        },
      ],
    });

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "选择 导师 11" }));
    fireEvent.click(screen.getByRole("button", { name: "批量改标签" }));
    fireEvent.click(await screen.findByRole("button", { name: "选择标签 高意愿" }));
    fireEvent.click(screen.getByRole("button", { name: "追加标签" }));

    await waitFor(() => {
      expect(bulkUpdateProfessorTags).toHaveBeenCalledWith({
        professor_ids: [11],
        mode: "add",
        tag_ids: [1],
      });
    });
    expect(await screen.findByText("高意愿")).toBeInTheDocument();
  });

  it("bulk updates tags from the management selection bar", async () => {
    const { bulkUpdateProfessorTags } = await import("@/lib/api/professorsApi");
    vi.mocked(bulkUpdateProfessorTags).mockResolvedValue({
      ok: true,
      affected_count: 1,
      message: "已更新 1 位导师的标签",
      professors: [
        {
          ...createManagementProfessor(11, "导师 11"),
          tags: [
            {
              id: 2,
              name: "已联系",
              text_color: "#1d4ed8",
              background_color: "#dbeafe",
            },
          ],
        },
      ],
    });

    render(
      <MemoryRouter>
        <ProfessorsPage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "选择 导师 11" }));
    fireEvent.click(screen.getByRole("button", { name: "批量改标签" }));
    fireEvent.click(await screen.findByRole("button", { name: "移除标签" }));
    fireEvent.click(screen.getByRole("button", { name: "选择标签 已联系" }));
    fireEvent.click(screen.getByRole("button", { name: "移除标签" }));

    await waitFor(() => {
      expect(bulkUpdateProfessorTags).toHaveBeenCalledWith({
        professor_ids: [11],
        mode: "remove",
        tag_ids: [2],
      });
    });
  });
```

- [ ] **步骤 2：运行页面测试验证失败**

运行：

```powershell
cd frontend
npm.cmd run test -- SelectionControls.test.tsx
```

预期：FAIL，找不到“批量改标签”按钮。

- [ ] **步骤 3：首页接入组件和 API**

在 `HomePage.tsx`：

- 从 lucide-react 引入 `Tags`。
- 引入 `BulkProfessorTagDialog`。
- 从 API 引入 `bulkUpdateProfessorTags`。
- 引入 `ProfessorBulkTagModeDTO` 类型。
- 新增状态 `bulkTagDialogOpen`、`savingBulkTags`。
- 新增 `openBulkTagDialog`、`closeBulkTagDialog`、`saveBulkTags`。
- sticky bar 中“清空选择”后添加按钮。
- render 末尾添加 `BulkProfessorTagDialog`。

保存逻辑：

```ts
  const saveBulkTags = async ({
    mode,
    tagIds,
  }: {
    mode: ProfessorBulkTagModeDTO;
    tagIds: number[];
  }) => {
    if (selectedIds.size === 0) {
      notifyWarning("请先选择导师", "选择至少一位导师后再批量修改标签。");
      return;
    }
    setSavingBulkTags(true);
    try {
      const result = await bulkUpdateProfessorTags({
        professor_ids: Array.from(selectedIds),
        mode,
        tag_ids: tagIds,
      });
      const tagsByProfessorId = new Map(
        result.professors.map((professor) => [professor.id, professor.tags]),
      );
      setProfessors((previous) =>
        previous.map((professor) => {
          const tags = tagsByProfessorId.get(professor.id);
          return tags ? { ...professor, tags } : professor;
        }),
      );
      notifySuccess("标签已更新", `已更新 ${result.affected_count} 位导师的标签。`);
      setBulkTagDialogOpen(false);
    } catch (saveError) {
      const message =
        saveError instanceof Error ? saveError.message : "批量修改标签失败";
      notifyError("批量修改标签失败", message);
    } finally {
      setSavingBulkTags(false);
    }
  };
```

- [ ] **步骤 4：导师管理页接入组件和 API**

在 `ProfessorsPage.tsx` 做同类修改。保存逻辑同首页，更新 `ProfessorManagementItemDTO[]` 的 `tags` 字段。

- [ ] **步骤 5：运行页面测试**

运行：

```powershell
cd frontend
npm.cmd run test -- SelectionControls.test.tsx
```

预期：PASS。

- [ ] **步骤 6：Commit 页面接入**

```powershell
git add frontend/src/pages/HomePage.tsx frontend/src/pages/ProfessorsPage.tsx frontend/src/pages/SelectionControls.test.tsx
git commit -m "feat(frontend): 接入批量导师标签操作"
```

## 任务 6：最终验证

**文件：**
- 验证：`backend/test/test_professor_tags.py`
- 验证：`frontend/src/components/molecules/BulkProfessorTagDialog.test.tsx`
- 验证：`frontend/src/pages/SelectionControls.test.tsx`

- [ ] **步骤 1：运行后端标签测试**

```powershell
cd backend
uv run python -m unittest test.test_professor_tags
```

预期：全部通过。

- [ ] **步骤 2：运行前端相关测试**

```powershell
cd frontend
npm.cmd run test -- BulkProfessorTagDialog.test.tsx SelectionControls.test.tsx
```

预期：全部通过。

- [ ] **步骤 3：运行前端 lint**

```powershell
cd frontend
npm.cmd run lint
```

预期：无 ESLint error。

- [ ] **步骤 4：检查工作区**

```powershell
git status --short --branch
```

预期：只剩用户已有未跟踪文件，功能相关文件均已提交。
