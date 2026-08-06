# LLM 模板保形改写设计

## 背景

当前模板编辑器支持比 LLM 草稿 schema 更丰富的格式，包括字体、字号、行距、首行缩进、对齐、表格、加粗、斜体、下划线、链接和列表。现有 LLM 草稿生成会把模板纯文本和完整 HTML 一起发给模型，要求模型返回 `rich_body` 受控富文本 JSON。`rich_body` 只支持段落、列表、加粗、斜体、链接和换行，因此字体、字号、行距、表格样式和部分局部格式会在 AI 改写后丢失。

目标是让 AI 改写后默认保留用户在模板编辑器中设置的格式。LLM 不再重建正文结构，也不再输出完整 HTML 或 `rich_body`；后端负责保留原 HTML，LLM 只返回可替换的文本。

## 目标

- AI 改写后保留模板编辑器当前支持的正文格式：字体、字号、行距、首行缩进、对齐、表格结构、表格样式、加粗、斜体、下划线、链接和列表。
- LLM 输入不包含完整模板 HTML，也不重复提供完整模板正文纯文本。
- LLM 只改写后端抽取出来的 `run.text`，不新增、删除、合并、拆分或重排 HTML 节点。
- 占位符不能被 LLM 改名、删除或新增；占位符所在 run 的格式必须保留。
- 没有返回的 run 保持原文，避免模型遗漏导致正文丢失。
- 第一版不允许 LLM 新增段落、表格行、列表项或任意新 run。

## 非目标

- 不做“AI 重新排版”或“AI 改样式”。
- 不让 LLM 直接输出 HTML。
- 不扩展第一版为任意布局编辑器。
- 不保证模型能把任意模板改得更好；本设计只保证格式保留和失败边界更可控。

## 当前问题

现有 prompt 给 LLM：

- 默认材料文本，超过 5000 字符截断。
- 导师信息、近期论文和当前匹配结果。
- 可选材料列表，只包含 id、名称和类型。
- 套磁信模板主题。
- 套磁信模板正文纯文本。
- 套磁信模板正文 HTML。
- 改写偏好和 rich_body 输出规则。

这会带来两个问题：

- token 重复：模板正文在纯文本和 HTML 中各出现一次。
- 格式不稳定：LLM 必须把 HTML 格式转成 `rich_body`，但 `rich_body` 不支持完整编辑器格式。

## 设计概览

新链路分四步：

1. 后端解析模板正文 HTML，抽取可编辑文本为 `segments` 和 `runs`，同时保存 run 到原 DOM 文本节点的映射。
2. 后端把占位符替换为锁定 token，例如 `[[PH_1]]`，并记录 token 与原占位符的映射。
3. LLM 输入只包含主题模板、正文 `segments/runs`、导师信息、材料摘要、匹配结果和改写偏好。
4. LLM 返回 `subject`、`replacements` 和 `suggested_material_ids`。后端校验后把文本回填到原 HTML，再还原占位符并做 HTML 清洗和可见文本校验。

## 数据模型

### Segment

`segment` 表示一个逻辑正文块。第一版支持：

- `paragraph`
- `heading`
- `list_item`
- `table_cell`

示例：

```json
{
  "segment_id": "seg_2",
  "role": "paragraph",
  "runs": [
    {
      "run_id": "run_1",
      "text": "我是"
    },
    {
      "run_id": "run_2",
      "text": "[[PH_1]]",
      "marks": ["strong", "placeholder"]
    },
    {
      "run_id": "run_3",
      "text": "，对您的研究很感兴趣。"
    }
  ]
}
```

### Run

`run` 表示一个 segment 中连续且格式相同的文本范围。后端保存 run 到 DOM 文本节点的映射，LLM 只看到简化字段：

- `run_id`
- `text`
- `marks`
- `locked_placeholders`

`marks` 只用于帮助 LLM 理解文本含义，不作为回填格式来源。真实格式仍以原 HTML DOM 为准。可用 marks：

- `strong`
- `emphasis`
- `underline`
- `link`
- `placeholder`

字体、字号、行距、缩进、对齐、表格边框等不发给 LLM。

## 占位符锁定

后端在发给 LLM 前把模板占位符替换为锁定 token：

```text
{{sender_name}} -> [[PH_1]]
{{research_direction}} -> [[PH_2]]
```

LLM 输入中记录：

```json
{
  "token": "[[PH_1]]",
  "original": "{{sender_name}}"
}
```

返回后校验：

- 每个 run 原有的 placeholder token 必须仍存在于同一个 run。
- 不允许新增未知 token。
- 不允许删除 token。
- 不允许把 token 移到其他 run。
- 校验通过后还原成原占位符。

如果校验失败，后端不使用该 run 的替换结果，默认保留原文，并记录诊断 metadata。第一版不因为单个 run 占位符校验失败而让整次生成失败，除非所有可编辑 run 都无有效结果。

## LLM 输入格式

第一版不提供完整 HTML 和完整正文纯文本。输入示例：

```json
{
  "task": "rewrite_email_template_runs_preserving_layout",
  "hard_rules": [
    "只返回 JSON，不要返回 HTML 或 Markdown。",
    "只能改写 replacements 中的 run.text。",
    "segment_id 和 run_id 必须来自 body_segments。",
    "不要新增、删除、合并、拆分或重排 segment/run。",
    "不要修改任何格式、样式、表格结构或链接地址。",
    "占位符 token 必须留在原 run 中，不能改写、删除、新增或移动。",
    "如果某个 run 不需要改写，可以不返回它。"
  ],
  "context": {
    "professor": {
      "name": "李老师",
      "university": "Example University",
      "department": "Computer Science",
      "research_direction": "Information Extraction",
      "recent_papers": [
        "Recent Advances in Biomedical Information Extraction"
      ]
    },
    "student": {
      "sender_name": "张三",
      "primary_material_excerpt": "我做过医学 NLP、信息抽取和大模型应用相关项目。"
    },
    "current_match": {
      "match_score": 88,
      "match_reason": "研究方向与项目经历有交集。",
      "fit_points": ["医学 NLP 项目相关"],
      "risk_points": [],
      "keywords": ["信息抽取", "医学 NLP"]
    },
    "rewrite_preferences": {
      "intensity": "moderate",
      "tone": "polite",
      "length": "default",
      "specificity": "balanced"
    }
  },
  "subject_template": "申请与[[PH_1]]老师交流[[PH_2]]方向",
  "subject_placeholders": [
    {
      "token": "[[PH_1]]",
      "original": "{{name}}"
    },
    {
      "token": "[[PH_2]]",
      "original": "{{research_direction}}"
    }
  ],
  "body_segments": [
    {
      "segment_id": "seg_1",
      "role": "paragraph",
      "runs": [
        {
          "run_id": "run_1",
          "text": "[[PH_1]]老师，您好：",
          "marks": ["placeholder"],
          "locked_placeholders": [
            {
              "token": "[[PH_1]]",
              "original": "{{name}}"
            }
          ]
        }
      ]
    },
    {
      "segment_id": "seg_2",
      "role": "paragraph",
      "runs": [
        {
          "run_id": "run_1",
          "text": "我是"
        },
        {
          "run_id": "run_2",
          "text": "[[PH_2]]",
          "marks": ["strong", "placeholder"],
          "locked_placeholders": [
            {
              "token": "[[PH_2]]",
              "original": "{{sender_name}}"
            }
          ]
        },
        {
          "run_id": "run_3",
          "text": "，目前正在准备申请。"
        }
      ]
    }
  ],
  "available_materials": [
    {
      "id": 7,
      "name": "简历",
      "type": "resume"
    }
  ]
}
```

## LLM 输出格式

LLM 只返回 JSON：

```json
{
  "subject": "申请与[[PH_1]]老师交流[[PH_2]]方向",
  "replacements": [
    {
      "segment_id": "seg_1",
      "runs": [
        {
          "run_id": "run_1",
          "text": "[[PH_1]]老师，您好："
        }
      ]
    },
    {
      "segment_id": "seg_2",
      "runs": [
        {
          "run_id": "run_1",
          "text": "我是"
        },
        {
          "run_id": "run_2",
          "text": "[[PH_2]]"
        },
        {
          "run_id": "run_3",
          "text": "，近期关注到您在信息抽取方向的研究，觉得与我的项目经历有较强关联。"
        }
      ]
    }
  ],
  "suggested_material_ids": [7]
}
```

## 回填规则

- 后端以原模板 HTML 为基础，不使用 LLM 返回内容生成新 HTML。
- 只替换通过校验的 run 文本节点。
- 未返回的 run 保留原文。
- 单个 run 校验失败时保留原文。
- 回填后还原占位符。
- 回填后执行 HTML 安全清洗和可见文本校验。
- `body_text` 从最终 HTML 派生。

## Token 控制

新链路不再给完整模板 HTML，也不再额外给完整正文纯文本。正文模板 token 来自 `segments/runs`。

材料仍沿用当前策略：

- 默认材料文本最多 5000 字符。
- 其他可选材料只给 id、名称和类型。

如果模板非常长，第一版可以先整体发送；后续再引入按 segment 分片改写。分片不是第一版目标。

## 错误处理

- LLM 返回非法 JSON：本次生成失败，沿用现有 LLMRuntimeError 流程。
- subject 占位符校验失败：保留原主题模板或使用安全 fallback。
- replacement 指向不存在的 segment/run：忽略该 replacement。
- run 占位符校验失败：忽略该 run 的替换，保留原文。
- 所有 run 都未产生有效替换：失败并提示“模型未返回可用改写内容”。
- 最终 HTML 缺少可见文本：失败并提示“模型未返回可用正文”。

## 兼容策略

- 模板模式不变，仍直接渲染模板并保留 HTML。
- LLM 草稿生成改用 segment/runs 保形改写。
- 如果模板没有 HTML，只提供纯文本模板，则后端先把纯文本转成基础段落 HTML，再抽取 segments/runs。
- 历史任务中已保存的 generated HTML 不迁移。

## 测试计划

- HTML 抽取：段落、标题、列表项、表格单元格能抽出稳定 segment/run。
- 局部格式：`strong`、`em`、`u`、`a` 包裹的文本回填后格式保留。
- 字体字号：`style` 中的 `font-family`、`font-size`、`line-height` 回填后保留。
- 表格：单元格文本替换后表格结构、边框样式、单元格属性保留。
- 占位符：删除、改名、新增、移动占位符都被拦截。
- LLM 输出：缺失 run、未知 run、空文本、非法 JSON 都有确定行为。
- 草稿生成：生成后的 `body_html` 和 `body_text` 可用于工作区预览、审核和发送。
- token 估算：不再把完整模板 HTML 和完整正文纯文本重复计入 prompt。

## 第一版范围决定

第一版严格禁止新增内容。LLM 只能改写已有 run，不能新增 segment、run、段落、列表项、表格行或表格单元格。若后续需要更强个性化，可以单独设计 `insert_after_run_id` 扩展，由后端复制相邻 run 或 segment 的格式插入文本。
