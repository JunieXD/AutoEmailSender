# AI 改写草稿格式保真重做 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把草稿改写从 `run/锚点` 局部改写切换为段落级结构化改写，表格原样保留，提示词暴露原文中的局部样式片段，输出直接是真实内容，并按原稿中占比最大的字体和字号统一落版。

**架构：** 新增一个草稿改写专用模块，专门负责 HTML 归一化、占位符真实值替换、prompt 载荷组装、LLM 返回的扁平 `runs` 回填，以及主字体/字号统计。`llm_runtime.py` 只保留生成流程编排和 API 入口，不再直接拼接旧的 run/anchor prompt。最终 HTML 仍走现有清洗链路，表格块保留原始 HTML fragment，不进入模型改写结果。

**技术栈：** Python 3.12、FastAPI、Pydantic、BeautifulSoup、httpx、`unittest`、`uv`

---

## 文件结构

- 新增 `backend/app/services/template_draft_rewrite.py`：草稿改写专用模块。负责把模板 HTML 归一化成 `source_blocks`，把占位符替换为真实值，构造给 LLM 的 payload，解析并回填扁平 `runs`，计算并应用主字体/字号。
- 修改 `backend/app/services/llm_runtime.py`：把 `generate_draft_content()` 和 `estimate_template_run_draft_tokens()` 切到新模块，新增新的 structured result model 和 prompt builder，去掉旧的 anchor/run 改写分支。
- 修改 `backend/app/services/outreach_templates.py`：只复用现有 `build_template_context()` 和 `render_template_string()` 作为占位符解析来源，不改变模板渲染契约。
- 新增 `backend/test/test_template_draft_rewrite.py`：覆盖 source block 抽取、prompt 载荷、回填、主字体/字号统计。
- 修改 `backend/test/test_llm_runtime.py`：覆盖 `generate_draft_content()` 的新 prompt、新结果 schema 和生成成功路径。
- 修改 `backend/test/test_api_endpoints.py`：补一条接口级草稿生成回归，确认任务入口仍返回正确的最终 HTML / text。

## 任务 1：抽取草稿改写源块并处理占位符

**文件：**
- 创建：`backend/app/services/template_draft_rewrite.py`
- 创建：`backend/test/test_template_draft_rewrite.py`

- [ ] **步骤 1：先写失败的测试，锁定 source block 结构和占位符替换**

```python
from app.services.outreach_templates import build_template_context
from app.services.template_draft_rewrite import build_draft_rewrite_document


def test_build_draft_rewrite_document_splits_blocks_and_replaces_placeholders() -> None:
    html = (
        '<p><strong>{{name}}</strong>老师，您好，<u>欢迎</u>您。</p>'
        '<table><tbody><tr><td>原表格</td></tr></tbody></table>'
    )
    context = build_template_context(identity, professor)

    document = build_draft_rewrite_document(html, context)

    assert document.blocks[0].segment_id == "seg_1"
    assert document.blocks[0].type == "paragraph"
    assert document.blocks[0].text == "李老师老师，您好，欢迎您。"
    assert document.blocks[0].style_spans == [
        {"text": "李老师", "marks": ["strong"]},
        {"text": "欢迎", "marks": ["underline"]},
    ]
    assert document.blocks[1].type == "table"
    assert document.blocks[1].html_fragment == '<table><tbody><tr><td>原表格</td></tr></tbody></table>'
```

- [ ] **步骤 2：运行测试，确认当前还没有这个新模块**

运行：

```bash
cd backend && uv run python -m unittest test.test_template_draft_rewrite -v
```

预期：失败，报 `ImportError` / `ModuleNotFoundError`，因为 `build_draft_rewrite_document()` 还不存在。

- [ ] **步骤 3：实现最少代码，让 source block 抽取和占位符替换通过**

```python
@dataclass(slots=True)
class DraftRewriteStyleSpan:
    text: str
    marks: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DraftRewriteSourceBlock:
    segment_id: str
    type: str
    text: str
    style_spans: list[DraftRewriteStyleSpan] = field(default_factory=list)
    html_fragment: str | None = None


def build_draft_rewrite_document(html: str, context: dict[str, str]) -> DraftRewriteDocument:
    soup = BeautifulSoup(html.strip(), "html.parser")
    blocks = _extract_source_blocks(soup, context)
    return DraftRewriteDocument(html=str(soup), blocks=blocks)
```

实现时要点：

- 只把 `paragraph`、`heading`、`list_item` 送进可改写块。
- `table` 只保留原始 HTML fragment。
- 占位符解析直接复用 `render_template_string()` 语义，最终 `text` 必须是真实内容。
- `style_spans` 只记录原文中的局部连续片段，不写“整段加粗”这种抽象描述。

- [ ] **步骤 4：运行测试，确认 source block 结构和占位符替换通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_template_draft_rewrite.DraftRewriteDocumentTests -v
```

预期：PASS。

- [ ] **步骤 5：提交这个阶段**

```bash
git add backend/app/services/template_draft_rewrite.py backend/test/test_template_draft_rewrite.py
git commit -m "feat(backend): add draft rewrite source blocks"
```

## 任务 2：改写 prompt 和 LLM 返回 schema

**文件：**
- 修改：`backend/app/services/llm_runtime.py`
- 修改：`backend/test/test_llm_runtime.py`

- [ ] **步骤 1：先写失败的测试，锁定新 prompt 和输出 schema**

```python
from app.services.llm_runtime import build_draft_rewrite_prompt


def test_build_draft_rewrite_prompt_includes_style_spans_and_excludes_table_markup() -> None:
    prompt = build_draft_rewrite_prompt(
        identity=identity,
        primary_material=primary_material,
        professor=professor,
        available_materials=[primary_material],
        source_blocks=document.blocks,
        current_match=current_match,
        rewrite_preferences=DraftRewritePreferences(),
    )

    assert "加粗文本：李老师" in prompt
    assert "下划线文本：欢迎" in prompt
    assert "{{name}}" not in prompt
    assert "<table" not in prompt
```

- [ ] **步骤 2：运行测试，确认当前旧的 anchor/run prompt 还在**

运行：

```bash
cd backend && uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_build_draft_prompt_requires_template_first_and_limits_changes -v
```

预期：失败，或断言不满足，因为现在还是旧的模板润色 / anchor 路径。

- [ ] **步骤 3：实现新的 structured result 和 prompt builder**

```python
class DraftRewriteRun(BaseModel):
    text: str
    marks: list[Literal["strong", "underline", "emphasis"]] = Field(default_factory=list)


class DraftRewriteSegmentReplacement(BaseModel):
    segment_id: str
    runs: list[DraftRewriteRun] = Field(default_factory=list)


class DraftRewriteResult(BaseModel):
    subject: str
    replacements: list[DraftRewriteSegmentReplacement] = Field(default_factory=list)
    suggested_material_ids: list[int] = Field(default_factory=list)
```

实现时要点：

- prompt 只出现 `source_blocks`，不再出现旧的 `rewrite_segments` / `body_segments` / `anchors`。
- 表格块在 prompt 里只保留只读说明，不带可被模型改写的表格 HTML。
- `style_spans` 要写成原文片段文本，例如 `加粗文本：xxxxx`、`下划线文本：yyyyy`。
- 模型返回的 `runs` 只能包含最终真实文本，不得再出现占位符。

- [ ] **步骤 4：运行测试，确认 prompt 和 schema 已切换**

运行：

```bash
cd backend && uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_build_draft_rewrite_prompt_includes_style_spans_and_excludes_table_markup -v
```

预期：PASS。

- [ ] **步骤 5：提交这个阶段**

```bash
git add backend/app/services/llm_runtime.py backend/test/test_llm_runtime.py
git commit -m "feat(backend): switch draft rewrite prompt schema"
```

## 任务 3：回填 HTML 并统一主字体 / 字号

**文件：**
- 修改：`backend/app/services/template_draft_rewrite.py`
- 修改：`backend/test/test_template_draft_rewrite.py`

- [ ] **步骤 1：先写失败的测试，锁定扁平 runs 回填和主字体 / 字号选择**

```python
from app.services.template_draft_rewrite import (
    apply_draft_rewrite_replacements,
    select_dominant_font_and_size,
)


def test_select_dominant_font_and_size_uses_visible_char_count() -> None:
    html = (
        '<p style="font-family:SimSun;font-size:12pt">短句。</p>'
        '<p style="font-family:Arial;font-size:14pt">这是一段明显更长的正文文本。</p>'
    )

    style = select_dominant_font_and_size(html)

    assert style.font_family == "Arial"
    assert style.font_size == "14pt"


def test_apply_draft_rewrite_replacements_keeps_table_and_renders_runs() -> None:
    result = apply_draft_rewrite_replacements(
        document,
        [
            {
                "segment_id": "seg_1",
                "runs": [
                    {"text": "李老师，您好："},
                    {"text": "欢迎", "marks": ["underline"]},
                ],
            }
        ],
    )

    assert "<u>欢迎</u>" in result.html
    assert "<table" in result.html
```

- [ ] **步骤 2：运行测试，确认当前还没有这些回填和样式选择能力**

运行：

```bash
cd backend && uv run python -m unittest test.test_template_draft_rewrite.DraftRewriteRenderTests -v
```

预期：失败，报 `AttributeError` / `NameError` / 断言失败，因为 `apply_draft_rewrite_replacements()` 和 `select_dominant_font_and_size()` 还没接上。

- [ ] **步骤 3：实现最少代码，把 runs 渲染成 HTML 并应用主字体 / 字号**

```python
def apply_draft_rewrite_replacements(
    document: DraftRewriteDocument,
    replacements: list[dict[str, object]],
) -> RichTextRenderResult:
    html = _render_replacements_to_html(document, replacements)
    dominant_style = select_dominant_font_and_size(document.html)
    html = _apply_base_font_and_size(html, dominant_style)
    return normalize_email_html(html)
```

实现时要点：

- 只修改被 LLM 返回的段落块。
- 未改写块保持原样。
- 表格块直接回填原 HTML fragment。
- `runs` 扁平化拼接后再按 `marks` 生成 `<strong>` / `<u>` / `<em>` 标签。
- 主字体 / 字号只从原稿统计，不从改写结果重算。

- [ ] **步骤 4：运行测试，确认 HTML 回填和样式统一通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_template_draft_rewrite.DraftRewriteRenderTests -v
```

预期：PASS。

- [ ] **步骤 5：提交这个阶段**

```bash
git add backend/app/services/template_draft_rewrite.py backend/test/test_template_draft_rewrite.py
git commit -m "feat(backend): render draft rewrite runs"
```

## 任务 4：切换正式生成链路并清理旧路径

**文件：**
- 修改：`backend/app/services/llm_runtime.py`
- 修改：`backend/test/test_llm_runtime.py`
- 修改：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：先写失败的集成测试，锁定正式生成链路已经切到新模块**

```python
async def test_generate_draft_content_uses_block_prompt_and_keeps_table_html(self) -> None:
    html = '<p>李老师，您好：</p><table><tbody><tr><td>原表格</td></tr></tbody></table>'
    with patch(
        "app.services.llm_runtime.request_chat_completion",
        return_value=ChatCompletionResult(content=raw),
    ) as request_mock:
        result = await generate_draft_content(
            identity=identity,
            primary_material=primary_material,
            llm_profile=llm_profile,
            professor=professor,
            available_materials=[primary_material],
            custom_subject="申请交流",
            custom_body_html=html,
        )

    payload = request_mock.call_args.args[1]
    assert "source_blocks" in payload["messages"][1]["content"]
    assert "rewrite_segments" not in payload["messages"][1]["content"]
    assert "<table" not in payload["messages"][1]["content"]
    assert "<table" in result.result.body_html
```

- [ ] **步骤 2：运行测试，确认当前还是旧的 run / anchor 路径**

运行：

```bash
cd backend && uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_generate_draft_content_uses_template_runs_without_full_html -v
```

预期：失败，因为实现还没有切换到新 payload。

- [ ] **步骤 3：把 `generate_draft_content()` 和 token 估算切到新模块**

```python
from app.services.template_draft_rewrite import (
    apply_draft_rewrite_replacements,
    build_draft_rewrite_document,
    build_draft_rewrite_prompt,
)
```

实现时要点：

- `generate_draft_content()` 只保留一个模板改写分支，不再在 `template_run_rewrite` / `template_anchor_rewrite` 之间分叉。
- `estimate_template_run_draft_tokens()` 直接复用新的 prompt builder。
- 接口返回仍然是 `subject`、`body_html`、`body_text`、`suggested_material_ids`，不改外部契约。
- `backend/test/test_api_endpoints.py` 只需要补一条接口级回归，确认任务入口输出的最终 HTML 里保留表格，且正文不再出现占位符。

- [ ] **步骤 4：运行测试，确认正式生成路径已切换**

运行：

```bash
cd backend && uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_generate_draft_content_uses_block_prompt_and_keeps_table_html -v
```

预期：PASS。

- [ ] **步骤 5：执行一次面向后端的回归检查并提交**

运行：

```bash
cd backend && uv run python -m unittest discover test -v
```

预期：草稿相关测试、API 测试和现有回归测试全部通过，没有旧的 anchor/run 分支残留在正式生成路径里。

提交：

```bash
git add backend/app/services/llm_runtime.py backend/test/test_llm_runtime.py backend/test/test_api_endpoints.py
git commit -m "feat(backend): switch draft rewrite flow to structured blocks"
```

## 自检

- 规格中的“段落级改写”由任务 1 和任务 2 覆盖。
- “表格原样保留”由任务 1、任务 3 和任务 4 覆盖。
- “原文中的局部格式片段进 prompt”由任务 1 和任务 2 覆盖。
- “占位符先替换为真实内容，输出不得再出现占位符”由任务 1 和任务 4 覆盖。
- “全文统一使用原稿中占比最大的字体和字号”由任务 3 覆盖。
- “LLM 输出直接是真实内容”由任务 2 和任务 4 覆盖。
