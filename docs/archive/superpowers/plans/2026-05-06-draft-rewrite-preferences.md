# 草稿改写偏好设置实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为普通用户提供结构化的 AI 草稿改写偏好设置，并在草稿生成 prompt 中安全生效。

**架构：** 运行时设置继续作为全局配置来源，新增 6 个枚举字段保存草稿改写偏好。后端把枚举归一化为受控 prompt 片段，正式任务和测试发信共用该片段。前端在“其他设置”中展示结构化选项和即时示例预览。

**技术栈：** FastAPI、SQLAlchemy、Alembic、Pydantic、unittest、Vite、React、TypeScript、Vitest、Testing Library。

---

## 当前上下文

工作区已有未提交的 `draft_max_tokens` 相关改动，涉及：

- `backend/app/models/app_setting.py`
- `backend/app/schemas/runtime_settings.py`
- `backend/app/services/runtime_settings.py`
- `backend/app/services/task_runtime.py`
- `backend/app/services/test_compose_runtime.py`
- `backend/test/test_runtime_settings_api.py`
- `frontend/src/components/molecules/OtherSettingsCard.tsx`
- `frontend/src/lib/api/runtimeSettings.ts`
- `frontend/test/OtherSettingsCard.test.tsx`
- `backend/alembic/versions/c1a9d4e7f8b2_add_draft_max_tokens_setting.py`

实现时必须保留这些改动，不要回退 `draft_max_tokens`。新增草稿偏好应与它共存。

## 文件结构

### 后端运行时设置

- 修改：`backend/app/models/app_setting.py`
  - 在 `AppSetting` 上新增 6 个字符串字段。
- 修改：`backend/app/schemas/runtime_settings.py`
  - 新增枚举类型别名或 `Literal` 约束。
  - 在 `RuntimeSettingsRead` 和 `RuntimeSettingsUpdate` 中加入 6 个字段。
- 修改：`backend/app/services/runtime_settings.py`
  - 序列化新增字段。
  - 更新日志 metadata 继续记录完整变更。
- 修改：`backend/alembic/versions/c1a9d4e7f8b2_add_draft_max_tokens_setting.py`
  - 如果该 migration 仍未提交，在其中追加新增列。
  - 如果它已提交，改为创建新 migration，`down_revision` 指向 `c1a9d4e7f8b2`。

### 后端 prompt

- 修改：`backend/app/services/llm_runtime.py`
  - 定义偏好 DTO 或轻量 dataclass。
  - 新增 `build_draft_rewrite_preferences()`。
  - 让 `generate_draft_content()` 和 `build_draft_prompt()` 接收偏好文本或偏好对象。
- 修改：`backend/app/services/task_runtime.py`
  - 正式任务 AI 草稿生成时传入运行时偏好。
- 修改：`backend/app/services/test_compose_runtime.py`
  - 测试发信 AI 草稿生成时传入运行时偏好。

### 前端设置页

- 修改：`frontend/src/lib/api/runtimeSettings.ts`
  - 扩展 DTO 类型。
  - 导出草稿偏好枚举 union、默认值和选项元数据。
- 修改：`frontend/src/components/molecules/OtherSettingsCard.tsx`
  - 将数值型设置和草稿偏好设置分区展示。
  - 新增“恢复默认”和即时示例预览。
- 修改：`frontend/test/OtherSettingsCard.test.tsx`
  - 覆盖加载、保存、恢复默认和示例预览。

### 测试

- 修改：`backend/test/test_database_schema.py`
  - 校验 `app_settings` 包含新增列。
- 修改：`backend/test/test_runtime_settings_api.py`
  - 校验默认值、更新、非法枚举。
- 修改：`backend/test/test_api_endpoints.py`
  - 校验正式草稿生成和测试发信把偏好传入 LLM runtime。
- 新增或修改：后端 LLM runtime 单测所在文件。
  - 如果已有 `backend/test/test_llm_runtime.py`，在其中添加 prompt 构造测试。
  - 如果没有，创建 `backend/test/test_llm_runtime.py`。

---

## 任务 1：后端运行时设置增加草稿偏好字段

**文件：**

- 修改：`backend/app/models/app_setting.py`
- 修改：`backend/app/schemas/runtime_settings.py`
- 修改：`backend/app/services/runtime_settings.py`
- 修改：`backend/alembic/versions/c1a9d4e7f8b2_add_draft_max_tokens_setting.py` 或新增后续 migration
- 修改：`backend/test/test_runtime_settings_api.py`
- 修改：`backend/test/test_database_schema.py`

- [ ] **步骤 1：编写失败的 API 测试**

在 `backend/test/test_runtime_settings_api.py` 的 `test_get_runtime_settings_returns_defaults` 中增加断言：

```python
self.assertEqual(payload["draft_rewrite_intensity"], "moderate")
self.assertEqual(payload["draft_rewrite_tone"], "polite")
self.assertEqual(payload["draft_rewrite_formality"], "balanced")
self.assertEqual(payload["draft_rewrite_length"], "default")
self.assertEqual(payload["draft_rewrite_specificity"], "balanced")
self.assertEqual(payload["draft_template_preservation"], "structure_first")
```

在 `test_patch_runtime_settings_updates_values_and_records_log` 的 JSON 请求体中加入：

```python
"draft_rewrite_intensity": "strong",
"draft_rewrite_tone": "professional",
"draft_rewrite_formality": "formal",
"draft_rewrite_length": "more_detailed",
"draft_rewrite_specificity": "detailed",
"draft_template_preservation": "content_first",
```

并增加响应断言：

```python
self.assertEqual(response.json()["draft_rewrite_intensity"], "strong")
self.assertEqual(response.json()["draft_rewrite_tone"], "professional")
self.assertEqual(response.json()["draft_rewrite_formality"], "formal")
self.assertEqual(response.json()["draft_rewrite_length"], "more_detailed")
self.assertEqual(response.json()["draft_rewrite_specificity"], "detailed")
self.assertEqual(response.json()["draft_template_preservation"], "content_first")
```

新增非法枚举测试：

```python
def test_patch_runtime_settings_rejects_invalid_draft_rewrite_preference(self) -> None:
    response = self.client.patch(
        "/api/runtime-settings",
        json={
            "match_analysis_job_worker_count": 1,
            "match_analysis_job_item_concurrency": 4,
            "match_analysis_job_interval_seconds": 5,
            "crawler_worker_count": 3,
            "crawler_profile_enrichment_concurrency": 4,
            "crawler_host_concurrency": 2,
            "draft_max_tokens": 4800,
            "draft_rewrite_intensity": "rewrite_everything",
            "draft_rewrite_tone": "polite",
            "draft_rewrite_formality": "balanced",
            "draft_rewrite_length": "default",
            "draft_rewrite_specificity": "balanced",
            "draft_template_preservation": "structure_first",
        },
    )

    self.assertEqual(response.status_code, 422)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest backend/test/test_runtime_settings_api.py
```

预期：失败，原因是响应缺少 `draft_rewrite_*` 或 schema 不接受这些字段。

- [ ] **步骤 3：实现模型和 schema 字段**

在 `backend/app/models/app_setting.py` 中引入 `String`：

```python
from sqlalchemy import DateTime, Integer, String, text
```

在 `AppSetting` 中新增字段：

```python
draft_rewrite_intensity: Mapped[str] = mapped_column(
    String(32),
    nullable=False,
    server_default=text("'moderate'"),
)
draft_rewrite_tone: Mapped[str] = mapped_column(
    String(32),
    nullable=False,
    server_default=text("'polite'"),
)
draft_rewrite_formality: Mapped[str] = mapped_column(
    String(32),
    nullable=False,
    server_default=text("'balanced'"),
)
draft_rewrite_length: Mapped[str] = mapped_column(
    String(32),
    nullable=False,
    server_default=text("'default'"),
)
draft_rewrite_specificity: Mapped[str] = mapped_column(
    String(32),
    nullable=False,
    server_default=text("'balanced'"),
)
draft_template_preservation: Mapped[str] = mapped_column(
    String(32),
    nullable=False,
    server_default=text("'structure_first'"),
)
```

在 `backend/app/schemas/runtime_settings.py` 中新增类型：

```python
from typing import Literal


DraftRewriteIntensity = Literal["light", "moderate", "strong"]
DraftRewriteTone = Literal["polite", "professional", "friendly"]
DraftRewriteFormality = Literal["natural", "balanced", "formal"]
DraftRewriteLength = Literal["shorter", "default", "more_detailed"]
DraftRewriteSpecificity = Literal["concise", "balanced", "detailed"]
DraftTemplatePreservation = Literal["structure_first", "balanced", "content_first"]
```

在 `RuntimeSettingsRead` 和 `RuntimeSettingsUpdate` 中加入：

```python
draft_rewrite_intensity: DraftRewriteIntensity
draft_rewrite_tone: DraftRewriteTone
draft_rewrite_formality: DraftRewriteFormality
draft_rewrite_length: DraftRewriteLength
draft_rewrite_specificity: DraftRewriteSpecificity
draft_template_preservation: DraftTemplatePreservation
```

- [ ] **步骤 4：实现序列化字段**

在 `backend/app/services/runtime_settings.py` 的 `serialize_runtime_settings()` 中加入：

```python
draft_rewrite_intensity=settings.draft_rewrite_intensity,
draft_rewrite_tone=settings.draft_rewrite_tone,
draft_rewrite_formality=settings.draft_rewrite_formality,
draft_rewrite_length=settings.draft_rewrite_length,
draft_rewrite_specificity=settings.draft_rewrite_specificity,
draft_template_preservation=settings.draft_template_preservation,
```

- [ ] **步骤 5：更新 migration**

如果 `backend/alembic/versions/c1a9d4e7f8b2_add_draft_max_tokens_setting.py` 仍未提交，在 `upgrade()` 的同一个 `batch_alter_table` 中追加：

```python
batch_op.add_column(sa.Column("draft_rewrite_intensity", sa.String(length=32), server_default=sa.text("'moderate'"), nullable=False))
batch_op.add_column(sa.Column("draft_rewrite_tone", sa.String(length=32), server_default=sa.text("'polite'"), nullable=False))
batch_op.add_column(sa.Column("draft_rewrite_formality", sa.String(length=32), server_default=sa.text("'balanced'"), nullable=False))
batch_op.add_column(sa.Column("draft_rewrite_length", sa.String(length=32), server_default=sa.text("'default'"), nullable=False))
batch_op.add_column(sa.Column("draft_rewrite_specificity", sa.String(length=32), server_default=sa.text("'balanced'"), nullable=False))
batch_op.add_column(sa.Column("draft_template_preservation", sa.String(length=32), server_default=sa.text("'structure_first'"), nullable=False))
```

并在 `downgrade()` 中按新增反序删除：

```python
batch_op.drop_column("draft_template_preservation")
batch_op.drop_column("draft_rewrite_specificity")
batch_op.drop_column("draft_rewrite_length")
batch_op.drop_column("draft_rewrite_formality")
batch_op.drop_column("draft_rewrite_tone")
batch_op.drop_column("draft_rewrite_intensity")
```

如果该 migration 已经提交，则新建 migration，例如：

```powershell
rtk uv run alembic revision -m "add draft rewrite preferences"
```

然后写入同样的 `add_column` / `drop_column` 内容，`down_revision` 指向 `c1a9d4e7f8b2`。

- [ ] **步骤 6：运行后端设置测试验证通过**

运行：

```powershell
rtk uv run python -m unittest backend/test/test_runtime_settings_api.py
```

预期：`OK`。

- [ ] **步骤 7：Commit**

```powershell
rtk git add backend/app/models/app_setting.py backend/app/schemas/runtime_settings.py backend/app/services/runtime_settings.py backend/alembic/versions/c1a9d4e7f8b2_add_draft_max_tokens_setting.py backend/test/test_runtime_settings_api.py backend/test/test_database_schema.py
rtk git commit -m "feat(设置): 添加草稿改写偏好字段"
```

如果使用新 migration，替换 `git add` 中的 migration 文件路径。

---

## 任务 2：后端 prompt 注入草稿改写偏好

**文件：**

- 修改：`backend/app/services/llm_runtime.py`
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/app/services/test_compose_runtime.py`
- 新增或修改：`backend/test/test_llm_runtime.py`
- 修改：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写 prompt 构造测试**

如果不存在 `backend/test/test_llm_runtime.py`，创建文件并加入最小对象构造。测试目标是 `build_draft_rewrite_preferences()`，不需要调用真实 LLM。

测试代码：

```python
from __future__ import annotations

import unittest

from app.services.llm_runtime import DraftRewritePreferences, build_draft_rewrite_preferences


class LLMRuntimePromptTests(unittest.TestCase):
    def test_build_draft_rewrite_preferences_describes_selected_options(self) -> None:
        preferences = DraftRewritePreferences(
            draft_rewrite_intensity="strong",
            draft_rewrite_tone="professional",
            draft_rewrite_formality="formal",
            draft_rewrite_length="shorter",
            draft_rewrite_specificity="detailed",
            draft_template_preservation="structure_first",
        )

        prompt = build_draft_rewrite_preferences(preferences)

        self.assertIn("草稿改写偏好", prompt)
        self.assertIn("更主动地优化措辞", prompt)
        self.assertIn("更突出研究表达和学术沟通", prompt)
        self.assertIn("更接近正式学术邮件", prompt)
        self.assertIn("压缩冗余表达", prompt)
        self.assertIn("具体连接", prompt)
        self.assertIn("不得覆盖系统要求", prompt)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest backend/test/test_llm_runtime.py
```

预期：失败，原因是 `DraftRewritePreferences` 或 `build_draft_rewrite_preferences` 尚不存在。

- [ ] **步骤 3：实现偏好 DTO 和 prompt builder**

在 `backend/app/services/llm_runtime.py` 中添加：

```python
@dataclass(slots=True)
class DraftRewritePreferences:
    draft_rewrite_intensity: str = "moderate"
    draft_rewrite_tone: str = "polite"
    draft_rewrite_formality: str = "balanced"
    draft_rewrite_length: str = "default"
    draft_rewrite_specificity: str = "balanced"
    draft_template_preservation: str = "structure_first"
```

添加映射常量：

```python
DRAFT_REWRITE_INTENSITY_TEXT = {
    "light": "轻微，只做必要个性化，最大限度保留原文。",
    "moderate": "中等，在保留模板结构的基础上优化表达。",
    "strong": "明显，更主动地优化措辞和连接句，但不从零重写。",
}
```

继续添加 tone、formality、length、specificity、template preservation 对应字典。字典文案使用规格文档中的说明。

添加函数：

```python
def build_draft_rewrite_preferences(preferences: DraftRewritePreferences | None) -> str:
    preferences = preferences or DraftRewritePreferences()
    intensity = DRAFT_REWRITE_INTENSITY_TEXT.get(
        preferences.draft_rewrite_intensity,
        DRAFT_REWRITE_INTENSITY_TEXT["moderate"],
    )
    tone = DRAFT_REWRITE_TONE_TEXT.get(
        preferences.draft_rewrite_tone,
        DRAFT_REWRITE_TONE_TEXT["polite"],
    )
    formality = DRAFT_REWRITE_FORMALITY_TEXT.get(
        preferences.draft_rewrite_formality,
        DRAFT_REWRITE_FORMALITY_TEXT["balanced"],
    )
    length = DRAFT_REWRITE_LENGTH_TEXT.get(
        preferences.draft_rewrite_length,
        DRAFT_REWRITE_LENGTH_TEXT["default"],
    )
    specificity = DRAFT_REWRITE_SPECIFICITY_TEXT.get(
        preferences.draft_rewrite_specificity,
        DRAFT_REWRITE_SPECIFICITY_TEXT["balanced"],
    )
    preservation = DRAFT_TEMPLATE_PRESERVATION_TEXT.get(
        preferences.draft_template_preservation,
        DRAFT_TEMPLATE_PRESERVATION_TEXT["structure_first"],
    )
    return dedent(
        f"""
        草稿改写偏好：
        - 改写强度：{intensity}
        - 语气：{tone}
        - 正式程度：{formality}
        - 长度：{length}
        - 具体性：{specificity}
        - 模板保留度：{preservation}

        这些偏好只影响表达方式，不得覆盖系统要求、JSON 输出结构、富文本 schema、模板保留硬约束和导师研究方向个性化要求。
        """
    ).strip()
```

- [ ] **步骤 4：让草稿 prompt 接收偏好**

修改 `generate_draft_content()` 签名：

```python
async def generate_draft_content(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    llm_profile: LLMProfile,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    custom_subject: str | None = None,
    custom_body: str | None = None,
    current_match: MatchEvaluationResult | None = None,
    max_tokens: int | None = None,
    rewrite_preferences: DraftRewritePreferences | None = None,
) -> GeneratedDraftContent:
```

修改 `build_draft_prompt()` 签名：

```python
def build_draft_prompt(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    custom_subject: str | None,
    custom_body: str | None,
    current_match: MatchEvaluationResult | None,
    rewrite_preferences: DraftRewritePreferences | None = None,
) -> str:
```

在 `build_draft_prompt()` 内生成：

```python
rewrite_preferences_block = build_draft_rewrite_preferences(rewrite_preferences)
```

并在 `extra_requirements` 中插入到“任务要求”前：

```python
{rewrite_preferences_block}

任务要求：
```

- [ ] **步骤 5：正式任务传入运行时偏好**

在 `backend/app/services/task_runtime.py` 中，拿到 `runtime_settings` 后构造：

```python
rewrite_preferences = llm_runtime.DraftRewritePreferences(
    draft_rewrite_intensity=runtime_settings.draft_rewrite_intensity,
    draft_rewrite_tone=runtime_settings.draft_rewrite_tone,
    draft_rewrite_formality=runtime_settings.draft_rewrite_formality,
    draft_rewrite_length=runtime_settings.draft_rewrite_length,
    draft_rewrite_specificity=runtime_settings.draft_rewrite_specificity,
    draft_template_preservation=runtime_settings.draft_template_preservation,
)
```

调用 `generate_draft_content()` 时加入：

```python
rewrite_preferences=rewrite_preferences,
```

- [ ] **步骤 6：测试发信传入运行时偏好**

在 `backend/app/services/test_compose_runtime.py` 中用相同方式构造 `DraftRewritePreferences`，并传入 `llm_runtime.generate_draft_content()`。

- [ ] **步骤 7：运行 prompt 测试验证通过**

运行：

```powershell
rtk uv run python -m unittest backend/test/test_llm_runtime.py
```

预期：`OK`。

- [ ] **步骤 8：运行相关 API 测试**

运行：

```powershell
rtk uv run python -m unittest backend/test/test_api_endpoints.py
```

预期：`OK`。如果现有测试中的 `AsyncMock` 对 `generate_draft_content()` 参数做严格断言，更新断言，确保包含 `rewrite_preferences`。

- [ ] **步骤 9：Commit**

```powershell
rtk git add backend/app/services/llm_runtime.py backend/app/services/task_runtime.py backend/app/services/test_compose_runtime.py backend/test/test_llm_runtime.py backend/test/test_api_endpoints.py
rtk git commit -m "feat(草稿): 按设置注入改写偏好"
```

---

## 任务 3：前端运行时设置接入草稿偏好

**文件：**

- 修改：`frontend/src/lib/api/runtimeSettings.ts`
- 修改：`frontend/src/components/molecules/OtherSettingsCard.tsx`
- 修改：`frontend/test/OtherSettingsCard.test.tsx`

- [ ] **步骤 1：编写前端失败测试**

在 `frontend/test/OtherSettingsCard.test.tsx` 的 mock 返回值中加入：

```typescript
draft_rewrite_intensity: "moderate",
draft_rewrite_tone: "polite",
draft_rewrite_formality: "balanced",
draft_rewrite_length: "default",
draft_rewrite_specificity: "balanced",
draft_template_preservation: "structure_first",
```

新增测试：

```typescript
it("loads saves and resets draft rewrite preferences", async () => {
  const api = await import("@/lib/api/runtimeSettings");

  render(<OtherSettingsCard />);
  fireEvent.click(screen.getByRole("button", { name: /其他设置/ }));

  expect(await screen.findByLabelText("改写强度")).toHaveValue("moderate");
  expect(screen.getByText(/示例效果/)).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("改写强度"), {
    target: { value: "strong" },
  });
  fireEvent.change(screen.getByLabelText("语气"), {
    target: { value: "professional" },
  });
  expect(screen.getByText(/更主动/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "恢复草稿默认" }));
  expect(screen.getByLabelText("改写强度")).toHaveValue("moderate");

  fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
  await waitFor(() => {
    expect(api.updateRuntimeSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        draft_rewrite_intensity: "moderate",
        draft_rewrite_tone: "polite",
        draft_rewrite_formality: "balanced",
        draft_rewrite_length: "default",
        draft_rewrite_specificity: "balanced",
        draft_template_preservation: "structure_first",
      }),
    );
  });
});
```

- [ ] **步骤 2：运行前端测试验证失败**

运行：

```powershell
rtk npm --prefix frontend test -- OtherSettingsCard
```

预期：失败，原因是 DTO 和 UI 尚未提供草稿偏好字段。

- [ ] **步骤 3：扩展前端 DTO 和选项元数据**

在 `frontend/src/lib/api/runtimeSettings.ts` 中新增类型：

```typescript
export type DraftRewriteIntensity = "light" | "moderate" | "strong";
export type DraftRewriteTone = "polite" | "professional" | "friendly";
export type DraftRewriteFormality = "natural" | "balanced" | "formal";
export type DraftRewriteLength = "shorter" | "default" | "more_detailed";
export type DraftRewriteSpecificity = "concise" | "balanced" | "detailed";
export type DraftTemplatePreservation = "structure_first" | "balanced" | "content_first";
```

在 `RuntimeSettingsDTO` 中新增：

```typescript
draft_rewrite_intensity: DraftRewriteIntensity;
draft_rewrite_tone: DraftRewriteTone;
draft_rewrite_formality: DraftRewriteFormality;
draft_rewrite_length: DraftRewriteLength;
draft_rewrite_specificity: DraftRewriteSpecificity;
draft_template_preservation: DraftTemplatePreservation;
```

导出默认值：

```typescript
export const defaultDraftRewritePreferences = {
  draft_rewrite_intensity: "moderate",
  draft_rewrite_tone: "polite",
  draft_rewrite_formality: "balanced",
  draft_rewrite_length: "default",
  draft_rewrite_specificity: "balanced",
  draft_template_preservation: "structure_first",
} satisfies Pick<
  RuntimeSettingsDTO,
  | "draft_rewrite_intensity"
  | "draft_rewrite_tone"
  | "draft_rewrite_formality"
  | "draft_rewrite_length"
  | "draft_rewrite_specificity"
  | "draft_template_preservation"
>;
```

- [ ] **步骤 4：拆分设置表单状态**

在 `OtherSettingsCard.tsx` 中把当前 `fields` 改名为 `numberFields`，保持 `draft_max_tokens` 和并发字段继续使用数字输入。

新增 `preferenceFields`：

```typescript
const preferenceFields = [
  {
    key: "draft_rewrite_intensity",
    label: "改写强度",
    hint: "控制 AI 对模板措辞的调整幅度。",
    options: [
      { value: "light", label: "轻微" },
      { value: "moderate", label: "中等" },
      { value: "strong", label: "明显" },
    ],
  },
  {
    key: "draft_rewrite_tone",
    label: "语气",
    hint: "控制邮件表达的沟通气质。",
    options: [
      { value: "polite", label: "礼貌" },
      { value: "professional", label: "专业" },
      { value: "friendly", label: "亲和" },
    ],
  },
  {
    key: "draft_rewrite_formality",
    label: "正式程度",
    hint: "控制句式接近自然表达还是正式学术邮件。",
    options: [
      { value: "natural", label: "更自然" },
      { value: "balanced", label: "默认" },
      { value: "formal", label: "更正式" },
    ],
  },
  {
    key: "draft_rewrite_length",
    label: "长度",
    hint: "控制 AI 是否压缩或展开模板内容。",
    options: [
      { value: "shorter", label: "更短" },
      { value: "default", label: "默认" },
      { value: "more_detailed", label: "更详细" },
    ],
  },
  {
    key: "draft_rewrite_specificity",
    label: "具体性",
    hint: "控制匹配理由的细节密度。",
    options: [
      { value: "concise", label: "概括" },
      { value: "balanced", label: "平衡" },
      { value: "detailed", label: "细节更足" },
    ],
  },
  {
    key: "draft_template_preservation",
    label: "模板保留度",
    hint: "控制 AI 对模板结构和主要话术的保留程度。",
    options: [
      { value: "structure_first", label: "优先保留结构" },
      { value: "balanced", label: "平衡" },
      { value: "content_first", label: "更重内容表达" },
    ],
  },
] as const;
```

更新 `toFormState()` 和 `toUpdatePayload()`：

```typescript
function toFormState(settings: RuntimeSettingsDTO): FormState {
  const state = { ...emptyForm };
  for (const field of numberFields) {
    state[field.key] = String(settings[field.key]);
  }
  for (const field of preferenceFields) {
    state[field.key] = String(settings[field.key]);
  }
  return state;
}
```

`toUpdatePayload()` 中数字字段转 `Number`，偏好字段直接赋值并兜底默认值。

- [ ] **步骤 5：实现偏好设置 UI 和恢复默认**

在设置内容中添加一个独立区块：

```tsx
<div className="space-y-3 border-t border-stone-200 pt-5">
  <div className="flex flex-wrap items-center justify-between gap-3">
    <div>
      <h3 className="text-base font-semibold text-stone-900">草稿改写偏好</h3>
      <p className="mt-1 text-sm text-stone-600">调整 AI 润色模板时的表达方式。</p>
    </div>
    <button type="button" className="ui-btn-secondary" onClick={resetDraftPreferences}>
      恢复草稿默认
    </button>
  </div>
  <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
    {preferenceFields.map((field) => (
      <label key={field.key} className="block rounded-xl border border-stone-200 bg-[#fcfbf8] px-4 py-4">
        <span className="text-sm font-semibold text-stone-900">{field.label}</span>
        <select
          aria-label={field.label}
          value={form[field.key]}
          onChange={(event) => handleChange(field.key, event.target.value)}
          className="mt-3 h-10 w-full rounded-xl border border-stone-200 bg-white px-3 text-sm text-stone-800 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
        >
          {field.options.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
        <span className="mt-2 block text-xs leading-5 text-stone-500">{field.hint}</span>
      </label>
    ))}
  </div>
</div>
```

实现 `resetDraftPreferences()`：

```typescript
const resetDraftPreferences = () => {
  setSavedMessage(null);
  setForm((current) => ({
    ...current,
    ...defaultDraftRewritePreferences,
  }));
};
```

- [ ] **步骤 6：实现即时示例预览**

新增函数：

```typescript
function buildDraftPreferencePreview(form: FormState): string {
  const intensity = form.draft_rewrite_intensity;
  const tone = form.draft_rewrite_tone;
  const length = form.draft_rewrite_length;

  if (intensity === "strong" && tone === "professional") {
    return "我认真关注了您在人工智能方向的研究，尤其希望结合自己的项目经历，进一步了解课题组当前关注的问题。";
  }
  if (length === "shorter") {
    return "我关注到您的人工智能研究方向，希望有机会进一步交流。";
  }
  return "我对您在人工智能方向的研究很感兴趣，希望结合自己的经历，进一步了解课题组的研究机会。";
}
```

在偏好区块下方渲染：

```tsx
<div className="rounded-xl border border-stone-200 bg-white px-4 py-4">
  <h4 className="text-sm font-semibold text-stone-900">示例效果</h4>
  <p className="mt-2 text-sm leading-6 text-stone-600">{buildDraftPreferencePreview(form)}</p>
</div>
```

- [ ] **步骤 7：运行前端测试验证通过**

运行：

```powershell
rtk npm --prefix frontend test -- OtherSettingsCard
```

预期：`PASS`。

- [ ] **步骤 8：Commit**

```powershell
rtk git add frontend/src/lib/api/runtimeSettings.ts frontend/src/components/molecules/OtherSettingsCard.tsx frontend/test/OtherSettingsCard.test.tsx
rtk git commit -m "feat(前端): 添加草稿改写偏好设置"
```

---

## 任务 4：真实预览 API（可独立提交）

**文件：**

- 修改：`backend/app/api/email_tasks.py`
- 修改：`backend/app/services/task_runtime.py`
- 新增或修改：`backend/app/schemas/email_tasks.py` 或现有任务 schema 文件
- 修改：`backend/test/test_api_endpoints.py`

如果当前阶段只做即时示例预览，本任务可以延后。若执行，则必须保证预览不保存草稿、不写正式草稿日志、不改变任务状态。

- [ ] **步骤 1：编写失败的 API 测试**

在 `backend/test/test_api_endpoints.py` 中新增测试：

```python
def test_preview_draft_uses_llm_without_saving_task_draft(self) -> None:
    task_id = self._create_llm_ready_email_task()
    with patch(
        "app.services.task_runtime.llm_runtime.generate_draft_content",
        new_callable=AsyncMock,
    ) as generate_mock:
        generate_mock.return_value = self._build_draft_generation_result(
            subject="预览主题",
            body_text="预览正文",
            body_html="<p>预览正文</p>",
        )

        response = self.client.post(f"/api/email-tasks/{task_id}/draft-preview")

    self.assertEqual(response.status_code, 200, msg=response.text)
    payload = response.json()
    self.assertEqual(payload["subject"], "预览主题")
    self.assertEqual(payload["body_text"], "预览正文")

    refreshed = self.client.get(f"/api/workspace/tasks/{task_id}")
    self.assertNotEqual(
        refreshed.json()["current_task"]["generated_subject"],
        "预览主题",
    )
```

如果项目中没有 `_create_llm_ready_email_task()` 辅助方法，复用同文件中生成 AI 草稿测试的任务创建逻辑。

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest backend/test/test_api_endpoints.py
```

预期：失败，原因是 `/api/email-tasks/{task_id}/draft-preview` 不存在。

- [ ] **步骤 3：实现服务函数**

在 `backend/app/services/task_runtime.py` 中新增：

```python
async def preview_task_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
) -> llm_runtime.GeneratedDraftContent:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        outreach_config = _resolve_task_outreach_config(task)
        if outreach_config.generation_mode == OUTREACH_GENERATION_MODE_TEMPLATE:
            raise ValueError("模板模式不需要 AI 草稿预览")
        if task.primary_material is None:
            raise ValueError("请先选择用于匹配的默认材料")
        if not _has_professor_research_direction(task.professor):
            raise ValueError("请先补充导师研究方向，再使用 AI 生成草稿")
        ensure_material_extracted_text(task.primary_material)
        detail = get_outreach_template_defaults_validation_error(
            _normalize_nullable_text(outreach_config.subject_template),
            _normalize_nullable_text(outreach_config.body_text_template),
        )
        if detail:
            raise ValueError(detail)
        runtime_settings = await get_runtime_settings(session)
        rewrite_preferences = llm_runtime.DraftRewritePreferences(
            draft_rewrite_intensity=runtime_settings.draft_rewrite_intensity,
            draft_rewrite_tone=runtime_settings.draft_rewrite_tone,
            draft_rewrite_formality=runtime_settings.draft_rewrite_formality,
            draft_rewrite_length=runtime_settings.draft_rewrite_length,
            draft_rewrite_specificity=runtime_settings.draft_rewrite_specificity,
            draft_template_preservation=runtime_settings.draft_template_preservation,
        )
        return await llm_runtime.generate_draft_content(
            identity=task.identity,
            primary_material=task.primary_material,
            llm_profile=task.llm_profile,
            professor=task.professor,
            available_materials=list(task.identity.materials),
            custom_subject=_normalize_nullable_text(outreach_config.subject_template),
            custom_body=_normalize_nullable_text(outreach_config.body_text_template),
            current_match=_build_match_result_from_task(task),
            max_tokens=runtime_settings.draft_max_tokens,
            rewrite_preferences=rewrite_preferences,
        )
```

该函数不得修改 `task.generated_subject`、不得新增 `EmailLog`、不得 `commit()`。

- [ ] **步骤 4：实现 API 路由和响应 schema**

在 `backend/app/api/email_tasks.py` 中新增 route：

```python
@router.post("/{task_id}/draft-preview", response_model=DraftPreviewRead)
async def preview_draft(task_id: int) -> DraftPreviewRead:
    return await run_async_endpoint(
        lambda: preview_task_draft(get_session_factory(), task_id),
        serializer=serialize_draft_preview,
    )
```

按项目现有 API helper 实际签名调整。如果没有 `serializer` 参数，则在 endpoint 中接收结果后手动序列化。

新增响应模型：

```python
class DraftPreviewUsageRead(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class DraftPreviewRead(BaseModel):
    subject: str
    body_text: str | None = None
    body_html: str | None = None
    rich_body: dict[str, object] | None = None
    suggested_material_ids: list[int] = Field(default_factory=list)
    usage: DraftPreviewUsageRead | None = None
```

- [ ] **步骤 5：运行 API 测试验证通过**

运行：

```powershell
rtk uv run python -m unittest backend/test/test_api_endpoints.py
```

预期：`OK`。

- [ ] **步骤 6：Commit**

```powershell
rtk git add backend/app/api/email_tasks.py backend/app/services/task_runtime.py backend/app/schemas/email_tasks.py backend/test/test_api_endpoints.py
rtk git commit -m "feat(草稿): 添加 AI 草稿真实预览接口"
```

如果响应 schema 放在其他现有 schema 文件中，替换 `git add` 路径。

---

## 任务 5：最终验证

**文件：**

- 不新增业务文件。
- 验证当前分支所有相关改动。

- [ ] **步骤 1：运行后端相关测试**

运行：

```powershell
rtk uv run python -m unittest backend/test/test_runtime_settings_api.py backend/test/test_api_endpoints.py backend/test/test_database_schema.py
```

预期：`OK`。

- [ ] **步骤 2：运行前端相关测试**

运行：

```powershell
rtk npm --prefix frontend test -- OtherSettingsCard
```

预期：`PASS`。

- [ ] **步骤 3：运行前端 lint**

运行：

```powershell
rtk npm --prefix frontend run lint
```

预期：退出码 `0`。

- [ ] **步骤 4：检查 diff**

运行：

```powershell
rtk git status --short
rtk git diff --stat
```

预期：只包含本功能相关文件。确认没有回退或覆盖用户已有改动。

- [ ] **步骤 5：处理验证补丁**

如果最终验证导致测试或格式小修，把修复归入对应任务的提交。比如只修了前端设置页测试，则使用任务 3 的 `git add` 文件清单和提交信息；只修了后端 API 测试，则使用任务 2 或任务 4 的 `git add` 文件清单和提交信息。如果没有新增改动，不需要提交。

---

## 自检

- 规格中的 6 个草稿偏好字段已覆盖：任务 1、任务 2、任务 3。
- “不暴露完整 system prompt”已覆盖：任务 2 只注入受控 prompt 片段，任务 3 只提供结构化选项。
- “配置缺失或非法回退默认”已覆盖：任务 1 的 schema 拒绝非法值，任务 2 的 prompt builder 对未知值兜底。
- “正式任务和测试发信都生效”已覆盖：任务 2 修改 `task_runtime.py` 和 `test_compose_runtime.py`。
- “即时示例预览”已覆盖：任务 3。
- “真实预览”已覆盖：任务 4，并标记为可独立提交。
- “不影响模板模式”已覆盖：任务 2 只在 LLM 生成路径传入偏好，任务 4 明确模板模式不需要 AI 预览。
