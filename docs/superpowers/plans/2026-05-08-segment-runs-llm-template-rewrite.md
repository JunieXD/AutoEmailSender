# LLM 模板保形改写实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 LLM 草稿生成从 `rich_body` 重建正文改为基于模板 HTML 的 `segment + runs` 文本替换，保留编辑器支持的字体、字号、行距、缩进、对齐、表格、链接和局部格式。

**架构：** 新增后端模板改写服务解析原 HTML，抽取可编辑 run 并锁定占位符；LLM 只接收 runs 并返回 replacements；后端校验后回填到原 HTML，再派生纯文本。现有模板模式保持不变，LLM 草稿模式改用新的保形改写结果。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy、Pydantic、BeautifulSoup、unittest、uv。

---

## 文件结构

- 创建：`backend/app/services/template_run_rewrite.py`
  - 负责解析模板 HTML、抽取 segment/run、占位符锁定、replacement 校验、HTML 回填、最终 HTML/text 渲染。
- 创建：`backend/test/test_template_run_rewrite.py`
  - 覆盖 HTML 抽取、局部格式保留、表格回填、占位符校验和异常边界。
- 修改：`backend/app/services/llm_runtime.py`
  - 新增保形改写 prompt、Pydantic 输出模型、调用入口，并让 `generate_draft_content()` 在有模板时使用 segment/run 路径。
- 修改：`backend/test/test_llm_runtime.py`
  - 覆盖 LLM payload 不包含完整 HTML/正文、输出 replacements 能生成保形 HTML。
- 修改：`backend/app/services/task_runtime.py`
  - 保持调用签名不变，确认 `custom_body_html` 继续传入 `generate_draft_content()`，必要时调整 provider payload 记录。
- 修改：`backend/app/api/workspace_support.py`
  - 更新 token 估算调用，避免继续按完整 HTML + 纯文本重复估算。
- 修改：`backend/test/test_batch_draft_generation_runtime.py`
  - 若现有 mock 受模型字段影响，补齐 `GeneratedDraftContent` 构造。

## 任务 1：实现模板 HTML 抽取与占位符锁定

**文件：**
- 创建：`backend/app/services/template_run_rewrite.py`
- 创建：`backend/test/test_template_run_rewrite.py`

- [ ] **步骤 1：编写失败测试：抽取段落、strong run 和占位符**

在 `backend/test/test_template_run_rewrite.py` 中新增：

```python
import unittest

from app.services.template_run_rewrite import build_template_run_document


class TemplateRunRewriteTests(unittest.TestCase):
    def test_extracts_runs_and_locks_placeholders(self) -> None:
        document = build_template_run_document(
            '<p style="font-family:SimSun;font-size:12pt">'
            '我对您的 <strong>{{research_direction}}</strong> 方向很感兴趣。'
            '</p>',
        )

        self.assertEqual(document.segments[0].segment_id, "seg_1")
        self.assertEqual(document.segments[0].role, "paragraph")
        self.assertEqual(
            [(run.run_id, run.text, run.marks) for run in document.segments[0].runs],
            [
                ("run_1", "我对您的 ", []),
                ("run_2", "[[PH_1]]", ["strong", "placeholder"]),
                ("run_3", " 方向很感兴趣。", []),
            ],
        )
        self.assertEqual(document.placeholders["[[PH_1]]"], "{{research_direction}}")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_template_run_rewrite.TemplateRunRewriteTests.test_extracts_runs_and_locks_placeholders
```

预期：失败，报错包含 `ModuleNotFoundError: No module named 'app.services.template_run_rewrite'` 或 `ImportError`。

- [ ] **步骤 3：实现最小数据模型和抽取逻辑**

在 `backend/app/services/template_run_rewrite.py` 中实现：

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag


PLACEHOLDER_PATTERN = re.compile(r"\{\{[a-zA-Z0-9_]+\}\}")


@dataclass(slots=True)
class TemplateRun:
    run_id: str
    text: str
    marks: list[str] = field(default_factory=list)
    locked_placeholders: list[dict[str, str]] = field(default_factory=list)
    node_index: int = 0


@dataclass(slots=True)
class TemplateSegment:
    segment_id: str
    role: str
    runs: list[TemplateRun]


@dataclass(slots=True)
class TemplateRunDocument:
    html: str
    soup: BeautifulSoup
    segments: list[TemplateSegment]
    placeholders: dict[str, str]
    nodes: list[NavigableString]


def build_template_run_document(html: str) -> TemplateRunDocument:
    soup = BeautifulSoup(html.strip(), "html.parser")
    placeholders: dict[str, str] = {}
    nodes: list[NavigableString] = []
    segments: list[TemplateSegment] = []

    for element in _iter_segment_elements(soup):
        runs: list[TemplateRun] = []
        for text_node in element.find_all(string=True, recursive=True):
            if not isinstance(text_node, NavigableString):
                continue
            text = str(text_node)
            if not text.strip():
                continue
            node_index = len(nodes)
            nodes.append(text_node)
            run_text, run_placeholders = _lock_placeholders(text, placeholders)
            runs.append(
                TemplateRun(
                    run_id=f"run_{len(runs) + 1}",
                    text=run_text,
                    marks=_collect_marks(text_node),
                    locked_placeholders=run_placeholders,
                    node_index=node_index,
                ),
            )
        if runs:
            segments.append(
                TemplateSegment(
                    segment_id=f"seg_{len(segments) + 1}",
                    role=_segment_role(element),
                    runs=runs,
                ),
            )

    return TemplateRunDocument(
        html=str(soup),
        soup=soup,
        segments=segments,
        placeholders=placeholders,
        nodes=nodes,
    )
```

同文件补充私有函数：

```python
def _iter_segment_elements(soup: BeautifulSoup) -> list[Tag]:
    tags = soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th"])
    if tags:
        return [tag for tag in tags if isinstance(tag, Tag)]
    body_text = soup.get_text("", strip=True)
    if body_text:
        wrapper = soup.new_tag("p")
        wrapper.string = body_text
        soup.clear()
        soup.append(wrapper)
        return [wrapper]
    return []


def _segment_role(element: Tag) -> str:
    if element.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return "heading"
    if element.name == "li":
        return "list_item"
    if element.name in {"td", "th"}:
        return "table_cell"
    return "paragraph"


def _collect_marks(text_node: NavigableString) -> list[str]:
    marks: list[str] = []
    for parent in text_node.parents:
        if not isinstance(parent, Tag):
            continue
        if parent.name in {"strong", "b"} and "strong" not in marks:
            marks.append("strong")
        if parent.name in {"em", "i"} and "emphasis" not in marks:
            marks.append("emphasis")
        if parent.name == "u" and "underline" not in marks:
            marks.append("underline")
        if parent.name == "a" and "link" not in marks:
            marks.append("link")
    return marks


def _lock_placeholders(
    text: str,
    placeholders: dict[str, str],
) -> tuple[str, list[dict[str, str]]]:
    locked: list[dict[str, str]] = []

    def replace(match: re.Match[str]) -> str:
        original = match.group(0)
        for token, value in placeholders.items():
            if value == original:
                locked.append({"token": token, "original": original})
                return token
        token = f"[[PH_{len(placeholders) + 1}]]"
        placeholders[token] = original
        locked.append({"token": token, "original": original})
        return token

    return PLACEHOLDER_PATTERN.sub(replace, text), locked
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test.test_template_run_rewrite.TemplateRunRewriteTests.test_extracts_runs_and_locks_placeholders
```

预期：`OK`。

- [ ] **步骤 5：Commit**

```powershell
rtk git add backend/app/services/template_run_rewrite.py backend/test/test_template_run_rewrite.py
rtk git commit -m "feat(backend): extract template rewrite runs"
```

## 任务 2：实现 replacements 回填和格式保留校验

**文件：**
- 修改：`backend/app/services/template_run_rewrite.py`
- 修改：`backend/test/test_template_run_rewrite.py`

- [ ] **步骤 1：编写失败测试：回填文本但保留段落样式、strong 和表格**

追加到 `TemplateRunRewriteTests`：

```python
from app.services.template_run_rewrite import apply_template_run_replacements


    def test_applies_replacements_preserving_styles_and_table(self) -> None:
        document = build_template_run_document(
            '<p style="font-family:SimSun;font-size:12pt">我对您的 '
            '<strong>{{research_direction}}</strong> 方向很感兴趣。</p>'
            '<table style="border-collapse:collapse"><tbody><tr>'
            '<td style="border:1px solid #ccc">研究经历</td>'
            '<td style="font-size:11pt">我做过信息抽取项目。</td>'
            '</tr></tbody></table>',
        )

        result = apply_template_run_replacements(
            document,
            [
                {
                    "segment_id": "seg_1",
                    "runs": [
                        {"run_id": "run_1", "text": "我近期关注到您在 "},
                        {"run_id": "run_2", "text": "[[PH_1]]"},
                        {"run_id": "run_3", "text": " 方向上的研究。"},
                    ],
                },
                {
                    "segment_id": "seg_3",
                    "runs": [
                        {
                            "run_id": "run_1",
                            "text": "我做过医学 NLP 与信息抽取项目。",
                        },
                    ],
                },
            ],
        )

        self.assertIn('style="font-family:SimSun;font-size:12pt"', result.html)
        self.assertIn("<strong>{{research_direction}}</strong>", result.html)
        self.assertIn("<table", result.html)
        self.assertIn('style="font-size:11pt"', result.html)
        self.assertIn("我做过医学 NLP 与信息抽取项目。", result.text)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_template_run_rewrite.TemplateRunRewriteTests.test_applies_replacements_preserving_styles_and_table
```

预期：失败，报错包含 `ImportError: cannot import name 'apply_template_run_replacements'`。

- [ ] **步骤 3：实现回填、占位符校验和结果模型**

在 `template_run_rewrite.py` 中新增：

```python
from app.services.rich_text import RichTextRenderResult, normalize_email_html


def apply_template_run_replacements(
    document: TemplateRunDocument,
    replacements: list[dict[str, Any]],
) -> RichTextRenderResult:
    segment_map = {segment.segment_id: segment for segment in document.segments}
    applied_count = 0

    for replacement in replacements:
        if not isinstance(replacement, dict):
            continue
        segment_id = replacement.get("segment_id")
        segment = segment_map.get(segment_id)
        if segment is None:
            continue
        run_map = {run.run_id: run for run in segment.runs}
        for run_replacement in replacement.get("runs", []):
            if not isinstance(run_replacement, dict):
                continue
            run = run_map.get(run_replacement.get("run_id"))
            text = run_replacement.get("text")
            if run is None or not isinstance(text, str):
                continue
            if not _replacement_preserves_placeholders(run, text):
                continue
            document.nodes[run.node_index].replace_with(_restore_placeholders(text, document.placeholders))
            applied_count += 1

    if applied_count == 0:
        raise ValueError("模型未返回可用改写内容")

    return normalize_email_html(str(document.soup))


def _replacement_preserves_placeholders(run: TemplateRun, text: str) -> bool:
    expected_tokens = {item["token"] for item in run.locked_placeholders}
    actual_tokens = set(re.findall(r"\[\[PH_\d+\]\]", text))
    return actual_tokens == expected_tokens


def _restore_placeholders(text: str, placeholders: dict[str, str]) -> str:
    restored = text
    for token, original in placeholders.items():
        restored = restored.replace(token, original)
    return restored
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test.test_template_run_rewrite.TemplateRunRewriteTests.test_applies_replacements_preserving_styles_and_table
```

预期：`OK`。

- [ ] **步骤 5：补占位符错误测试**

追加测试：

```python
    def test_invalid_placeholder_replacement_keeps_original_run(self) -> None:
        document = build_template_run_document(
            '<p>我是<strong>{{sender_name}}</strong>，您好。</p>',
        )

        result = apply_template_run_replacements(
            document,
            [
                {
                    "segment_id": "seg_1",
                    "runs": [
                        {"run_id": "run_1", "text": "我是"},
                        {"run_id": "run_2", "text": "张三"},
                        {"run_id": "run_3", "text": "，想和您交流。"},
                    ],
                },
            ],
        )

        self.assertIn("<strong>{{sender_name}}</strong>", result.html)
        self.assertIn("想和您交流", result.text)
```

- [ ] **步骤 6：运行模板回填测试文件**

运行：

```powershell
rtk uv run python -m unittest test.test_template_run_rewrite
```

预期：全部 `OK`。

- [ ] **步骤 7：Commit**

```powershell
rtk git add backend/app/services/template_run_rewrite.py backend/test/test_template_run_rewrite.py
rtk git commit -m "feat(backend): apply template run replacements"
```

## 任务 3：新增 LLM 保形改写输出模型和 prompt

**文件：**
- 修改：`backend/app/services/llm_runtime.py`
- 修改：`backend/test/test_llm_runtime.py`

- [ ] **步骤 1：编写失败测试：LLM payload 不包含完整模板 HTML 和正文纯文本**

在 `backend/test/test_llm_runtime.py` 中追加异步测试：

```python
    async def test_generate_draft_content_sends_template_runs_without_full_html(self) -> None:
        from app.models import IdentityMaterial, IdentityProfile, Professor

        identity = IdentityProfile(
            id=3,
            name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        primary_material = IdentityMaterial(
            id=7,
            identity_id=3,
            display_name="简历",
            file_path="data/materials/resume.txt",
            original_filename="resume.txt",
            material_type="resume",
            extracted_text="我做过医学 NLP 和信息抽取项目。",
        )
        profile = LLMProfile(
            id=5,
            name="openai",
            provider="openai",
            api_base_url=None,
            api_key="test-key",
            model_name="gpt-test",
        )
        professor = Professor(
            name="李老师",
            email="prof@example.edu",
            title="Professor",
            university="Example University",
            school="Computer Science",
            research_direction="Information Extraction",
        )
        calls: list[tuple[str, dict[str, object] | None]] = []
        responses = [
            _FakeResponse(
                status_code=200,
                payload={
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"subject":"申请交流","replacements":['
                                    '{"segment_id":"seg_1","runs":[{"run_id":"run_1","text":"[[PH_1]]老师，您好："}]},'
                                    '{"segment_id":"seg_2","runs":[{"run_id":"run_1","text":"我近期关注到您在 "},'
                                    '{"run_id":"run_2","text":"[[PH_2]]"},'
                                    '{"run_id":"run_3","text":" 方向的研究。"}]}'
                                    '],"suggested_material_ids":[7]}'
                                ),
                            },
                        },
                    ],
                },
            ),
        ]

        with patch(
            "app.services.llm_runtime.httpx.AsyncClient",
            side_effect=lambda *args, **kwargs: _FakeAsyncClient(responses, calls),
        ):
            result = await generate_draft_content(
                identity=identity,
                primary_material=primary_material,
                llm_profile=profile,
                professor=professor,
                available_materials=[primary_material],
                custom_subject="申请与{{name}}老师交流",
                custom_body="{{name}}老师，您好：\n我对您的 {{research_direction}} 方向很感兴趣。",
                custom_body_html=(
                    '<p style="font-family:SimSun">{{name}}老师，您好：</p>'
                    '<p>我对您的 <strong>{{research_direction}}</strong> 方向很感兴趣。</p>'
                ),
                max_tokens=4800,
            )

        prompt = calls[0][1]["messages"][1]["content"]
        self.assertIn("body_segments", prompt)
        self.assertNotIn("<p style=", prompt)
        self.assertNotIn("套磁信模板正文 HTML", prompt)
        self.assertIn('style="font-family:SimSun"', result.result.body_html)
        self.assertIn("<strong>{{research_direction}}</strong>", result.result.body_html)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_generate_draft_content_sends_template_runs_without_full_html
```

预期：失败。当前实现会在 prompt 中包含 `<p style=` 和“套磁信模板正文 HTML”。

- [ ] **步骤 3：新增 Pydantic 模型**

在 `llm_runtime.py` 的 `DraftGenerationResult` 附近新增：

```python
class TemplateRunReplacement(BaseModel):
    run_id: str
    text: str


class TemplateSegmentReplacement(BaseModel):
    segment_id: str
    runs: list[TemplateRunReplacement] = Field(default_factory=list)


class TemplateRunRewriteResult(BaseModel):
    subject: str
    replacements: list[TemplateSegmentReplacement] = Field(default_factory=list)
    suggested_material_ids: list[int] = Field(default_factory=list)
```

- [ ] **步骤 4：新增系统 prompt 和 prompt 构造函数**

在 `SYSTEM_DRAFT_PROMPT` 附近新增：

```python
SYSTEM_TEMPLATE_RUN_REWRITE_PROMPT = dedent(
    """
    你是研究生套磁邮件改写助理。你必须只输出 JSON。
    你不能输出 HTML、Markdown 或解释。
    你只能改写 body_segments 中已有 run 的 text。
    你不能新增、删除、合并、拆分或重排 segment/run。
    你不能修改任何格式、样式、表格结构或链接地址。
    占位符 token 例如 [[PH_1]] 必须留在原 run 中，不能改写、删除、新增或移动。

    JSON 字段必须包含：
    - subject: 邮件主题
    - replacements: segment 替换数组
    - suggested_material_ids: 整数数组，只能从可选材料 ID 中选择
    """
).strip()
```

新增函数：

```python
def build_template_run_rewrite_prompt(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    subject_template: str | None,
    template_document,
    current_match: MatchEvaluationResult | None,
    rewrite_preferences: DraftRewritePreferences | None,
) -> str:
    primary_material_text = (primary_material.extracted_text if primary_material else "") or ""
    if len(primary_material_text) > 5000:
        primary_material_text = f"{primary_material_text[:5000]}\n...(已截断)"
    payload = {
        "task": "rewrite_email_template_runs_preserving_layout",
        "context": {
            "identity": {
                "name": identity.name,
                "email_address": identity.email_address,
                "default_language": identity.default_language,
            },
            "professor": {
                "name": professor.name,
                "email": professor.email,
                "university": professor.university,
                "department": professor.department,
                "research_direction": professor.research_direction,
                "recent_papers": professor.recent_papers or [],
            },
            "student": {
                "primary_material_excerpt": primary_material_text,
            },
            "current_match": (
                current_match.model_dump() if current_match is not None else None
            ),
            "rewrite_preferences": (
                rewrite_preferences or DraftRewritePreferences()
            ).__dict__,
        },
        "subject_template": subject_template or "无",
        "body_segments": [
            {
                "segment_id": segment.segment_id,
                "role": segment.role,
                "runs": [
                    {
                        "run_id": run.run_id,
                        "text": run.text,
                        "marks": run.marks,
                        "locked_placeholders": run.locked_placeholders,
                    }
                    for run in segment.runs
                ],
            }
            for segment in template_document.segments
        ],
        "available_materials": [
            {
                "id": material.id,
                "name": material.display_name,
                "type": material.material_type,
            }
            for material in available_materials
        ],
    }
    return json.dumps(payload, ensure_ascii=False)
```

- [ ] **步骤 5：实现 `generate_draft_content()` 的模板 HTML 分支**

在 `llm_runtime.py` 顶部导入：

```python
from app.services.template_run_rewrite import (
    apply_template_run_replacements,
    build_template_run_document,
)
```

在 `generate_draft_content()` 开头增加：

```python
    if custom_body_html:
        template_document = build_template_run_document(custom_body_html)
        prompt = build_template_run_rewrite_prompt(
            identity=identity,
            primary_material=primary_material,
            professor=professor,
            available_materials=available_materials,
            subject_template=custom_subject,
            template_document=template_document,
            current_match=current_match,
            rewrite_preferences=rewrite_preferences,
        )
        completion = await request_chat_completion(
            llm_profile,
            {
                "model": llm_profile.model_name,
                "messages": [
                    {"role": "system", "content": SYSTEM_TEMPLATE_RUN_REWRITE_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": llm_profile.temperature if llm_profile.temperature is not None else DEFAULT_LLM_TEMPERATURE,
                "max_tokens": max_tokens or DEFAULT_LLM_MAX_TOKENS,
            },
        )
        rewrite_result = parse_structured_result(completion.content, TemplateRunRewriteResult)
        rendered = apply_template_run_replacements(
            template_document,
            [item.model_dump() for item in rewrite_result.replacements],
        )
        valid_material_ids = {material.id for material in available_materials}
        return GeneratedDraftContent(
            result=DraftGenerationResult(
                subject=rewrite_result.subject,
                body_text=rendered.text,
                body_html=rendered.html,
                suggested_material_ids=[
                    material_id
                    for material_id in rewrite_result.suggested_material_ids
                    if material_id in valid_material_ids
                ],
            ),
            usage=completion.usage,
        )
```

更新 `StructuredResultT`，加入 `TemplateRunRewriteResult`。

在 `parse_structured_result()` 中对 `TemplateRunRewriteResult` 做 subject 和 material id 基础标准化：

```python
    if isinstance(result, TemplateRunRewriteResult):
        result.subject = _normalize_text_field(result.subject, "subject")
        result.suggested_material_ids = _normalize_integer_list(result.suggested_material_ids)
        return result  # type: ignore[return-value]
```

- [ ] **步骤 6：运行 LLM 单测验证通过**

运行：

```powershell
rtk uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_generate_draft_content_sends_template_runs_without_full_html
```

预期：`OK`。

- [ ] **步骤 7：Commit**

```powershell
rtk git add backend/app/services/llm_runtime.py backend/test/test_llm_runtime.py
rtk git commit -m "feat(backend): rewrite llm drafts with template runs"
```

## 任务 4：支持纯文本模板 fallback 并保持现有 rich_body 路径兼容

**文件：**
- 修改：`backend/app/services/llm_runtime.py`
- 修改：`backend/test/test_llm_runtime.py`

- [ ] **步骤 1：编写失败测试：只有纯文本模板时也走保形 HTML**

在 `test_llm_runtime.py` 追加：

```python
    async def test_generate_draft_content_converts_text_template_to_runs(self) -> None:
        from app.models import IdentityMaterial, IdentityProfile, Professor

        identity = IdentityProfile(
            id=3,
            name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        primary_material = IdentityMaterial(
            id=7,
            identity_id=3,
            display_name="简历",
            file_path="data/materials/resume.txt",
            original_filename="resume.txt",
            material_type="resume",
            extracted_text="我做过信息抽取项目。",
        )
        profile = LLMProfile(
            id=5,
            name="openai",
            provider="openai",
            api_base_url=None,
            api_key="test-key",
            model_name="gpt-test",
        )
        professor = Professor(
            name="李老师",
            email="prof@example.edu",
            research_direction="Information Extraction",
        )
        calls: list[tuple[str, dict[str, object] | None]] = []
        responses = [
            _FakeResponse(
                status_code=200,
                payload={
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"subject":"申请交流","replacements":['
                                    '{"segment_id":"seg_1","runs":[{"run_id":"run_1","text":"李老师，您好："}]}'
                                    '],"suggested_material_ids":[]}'
                                ),
                            },
                        },
                    ],
                },
            ),
        ]

        with patch(
            "app.services.llm_runtime.httpx.AsyncClient",
            side_effect=lambda *args, **kwargs: _FakeAsyncClient(responses, calls),
        ):
            result = await generate_draft_content(
                identity=identity,
                primary_material=primary_material,
                llm_profile=profile,
                professor=professor,
                available_materials=[primary_material],
                custom_subject="申请交流",
                custom_body="老师您好：",
                custom_body_html=None,
            )

        self.assertIn("<p>李老师，您好：</p>", result.result.body_html)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_generate_draft_content_converts_text_template_to_runs
```

预期：失败。当前只有 `custom_body_html` 分支会进入 runs 路径。

- [ ] **步骤 3：让纯文本模板转换为基础 HTML**

在 `generate_draft_content()` 分支条件前生成：

```python
    template_html = custom_body_html
    if not template_html and custom_body:
        template_html = text_to_email_html(custom_body).html

    if template_html:
        template_document = build_template_run_document(template_html)
        ...
```

确保后续分支使用 `template_html`，不要使用 `custom_body_html`。

- [ ] **步骤 4：更新原有 max_tokens 测试的 fake response**

在 `test_generate_draft_content_uses_global_max_tokens_argument` 中，把 fake LLM 返回内容改成 replacements 形状：

```python
"content": (
    '{"subject":"申请交流","replacements":['
    '{"segment_id":"seg_1","runs":[{"run_id":"run_1","text":"模板主题"}]},'
    '{"segment_id":"seg_2","runs":[{"run_id":"run_1","text":"模板正文"}]}'
    '],"suggested_material_ids":[7]}'
),
```

该测试继续保留：

```python
self.assertEqual(payload["max_tokens"], 4800)
self.assertEqual(result.result.suggested_material_ids, [7])
```

- [ ] **步骤 5：运行新增测试和原有 max_tokens 测试**

运行：

```powershell
rtk uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_generate_draft_content_converts_text_template_to_runs
rtk uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_generate_draft_content_uses_global_max_tokens_argument
```

预期：两个测试均 `OK`。

- [ ] **步骤 6：Commit**

```powershell
rtk git add backend/app/services/llm_runtime.py backend/test/test_llm_runtime.py
rtk git commit -m "feat(backend): support text templates in run rewrite"
```

## 任务 5：更新 token 估算，避免模板正文重复计数

**文件：**
- 修改：`backend/app/services/llm_runtime.py`
- 修改：`backend/app/api/workspace_support.py`
- 修改：`backend/test/test_llm_runtime.py`

- [ ] **步骤 1：编写失败测试：新增 token 估算函数不计入完整 HTML**

在 `test_llm_runtime.py` 追加同步测试：

```python
    def test_estimate_template_run_draft_tokens_omits_full_html_snapshot(self) -> None:
        from app.models import IdentityMaterial, IdentityProfile, Professor

        identity = IdentityProfile(
            id=3,
            name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        material = IdentityMaterial(
            id=7,
            identity_id=3,
            display_name="简历",
            file_path="resume.txt",
            original_filename="resume.txt",
            material_type="resume",
            extracted_text="信息抽取经历",
        )
        profile = LLMProfile(
            id=5,
            name="openai",
            provider="openai",
            api_base_url=None,
            api_key="test-key",
            model_name="gpt-test",
        )
        professor = Professor(name="李老师", research_direction="Information Extraction")

        estimate = estimate_template_run_draft_tokens(
            identity=identity,
            primary_material=material,
            llm_profile=profile,
            professor=professor,
            available_materials=[material],
            custom_subject="申请交流",
            custom_body="老师您好：",
            custom_body_html='<p style="font-family:SimSun;font-size:12pt">老师您好：</p>',
            max_tokens=4800,
        )

        self.assertGreater(estimate.estimated_prompt_tokens, 0)
        self.assertEqual(estimate.estimated_completion_tokens_upper_bound, 4800)
        self.assertLess(estimate.estimated_prompt_tokens, 1200)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_estimate_template_run_draft_tokens_omits_full_html_snapshot
```

预期：失败，报错包含 `NameError: name 'estimate_template_run_draft_tokens' is not defined` 或 `ImportError`。

- [ ] **步骤 3：新增估算函数**

在 `llm_runtime.py` 中新增：

```python
def estimate_template_run_draft_tokens(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    llm_profile: LLMProfile,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    custom_subject: str | None = None,
    custom_body: str | None = None,
    custom_body_html: str | None = None,
    max_tokens: int | None = None,
) -> DraftTokenEstimate:
    template_html = custom_body_html or (text_to_email_html(custom_body).html if custom_body else "")
    if template_html:
        document = build_template_run_document(template_html)
        prompt = build_template_run_rewrite_prompt(
            identity=identity,
            primary_material=primary_material,
            professor=professor,
            available_materials=available_materials,
            subject_template=custom_subject,
            template_document=document,
            current_match=None,
            rewrite_preferences=DraftRewritePreferences(),
        )
        estimated_prompt_tokens = estimate_text_tokens(
            f"{SYSTEM_TEMPLATE_RUN_REWRITE_PROMPT}\n{prompt}",
        )
    else:
        estimated_prompt_tokens = estimate_text_tokens(
            f"{SYSTEM_DRAFT_PROMPT}\n"
            f"{build_draft_prompt(identity=identity, primary_material=primary_material, professor=professor, available_materials=available_materials, custom_subject=custom_subject, custom_body=custom_body, custom_body_html=custom_body_html, current_match=None)}",
        )
    estimated_completion_tokens_upper_bound = max_tokens or DEFAULT_LLM_MAX_TOKENS
    return DraftTokenEstimate(
        estimated_prompt_tokens=estimated_prompt_tokens,
        estimated_completion_tokens_upper_bound=estimated_completion_tokens_upper_bound,
        estimated_total_tokens_upper_bound=estimated_prompt_tokens + estimated_completion_tokens_upper_bound,
    )
```

- [ ] **步骤 4：更新 workspace token 估算调用**

在 `backend/app/api/workspace_support.py` 中，把 `estimate_match_and_draft_tokens(...)` 调用替换为：

```python
token_estimate = llm_runtime.estimate_template_run_draft_tokens(
    identity=identity,
    primary_material=current_task.primary_material,
    llm_profile=llm_profile,
    professor=professor,
    available_materials=list(identity.materials),
    custom_subject=current_task_outreach.subject_template,
    custom_body=llm_runtime.resolve_template_text(
        current_task_outreach.body_text_template,
        current_task_outreach.body_html_template,
    ),
    custom_body_html=current_task_outreach.body_html_template,
)
```

`workspace_support.py` 当前没有运行时设置对象，因此此处不传 `max_tokens`，继续使用全局默认输出 token 上限估算。

- [ ] **步骤 5：运行相关测试**

运行：

```powershell
rtk uv run python -m unittest test.test_llm_runtime
```

预期：全部 `OK`。

- [ ] **步骤 6：Commit**

```powershell
rtk git add backend/app/services/llm_runtime.py backend/app/api/workspace_support.py backend/test/test_llm_runtime.py
rtk git commit -m "feat(backend): estimate run rewrite draft tokens"
```

## 任务 6：集成验证草稿生成运行时

**文件：**
- 修改：`backend/test/test_batch_draft_generation_runtime.py`
- 修改：`backend/test/test_llm_runtime.py`
- 修改：`backend/test/test_template_run_rewrite.py`

- [ ] **步骤 1：确认 batch fake draft 结果字段完整**

在 `backend/test/test_batch_draft_generation_runtime.py` 的 `_build_draft_generation_result()` 中确保返回字段是最终草稿形态：

```python
return llm_runtime.GeneratedDraftContent(
    result=llm_runtime.DraftGenerationResult(
        subject="生成主题",
        body_text="生成正文",
        body_html="<p>生成正文</p>",
        suggested_material_ids=[],
    ),
    usage=llm_runtime.ChatCompletionUsage(
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    ),
)
```

- [ ] **步骤 2：运行现有草稿生成邻近测试**

运行：

```powershell
rtk uv run python -m unittest test.test_template_run_rewrite
rtk uv run python -m unittest test.test_llm_runtime
rtk uv run python -m unittest test.test_batch_draft_generation_runtime
```

预期：全部 `OK`。

- [ ] **步骤 3：补端到端单测：LLM 改写后任务保存保形 HTML**

在 `backend/test/test_llm_runtime.py` 追加服务级测试：

```python
    async def test_generate_draft_content_preserves_table_and_inline_styles(self) -> None:
        from app.models import IdentityMaterial, IdentityProfile, Professor

        identity = IdentityProfile(
            id=3,
            name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        primary_material = IdentityMaterial(
            id=7,
            identity_id=3,
            display_name="简历",
            file_path="data/materials/resume.txt",
            original_filename="resume.txt",
            material_type="resume",
            extracted_text="我做过医学 NLP 和信息抽取项目。",
        )
        profile = LLMProfile(
            id=5,
            name="openai",
            provider="openai",
            api_base_url=None,
            api_key="test-key",
            model_name="gpt-test",
        )
        professor = Professor(
            name="李老师",
            email="prof@example.edu",
            title="Professor",
            university="Example University",
            school="Computer Science",
            research_direction="Information Extraction",
        )
        calls: list[tuple[str, dict[str, object] | None]] = []
        responses = [
            _FakeResponse(
                status_code=200,
                payload={
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"subject":"申请交流","replacements":['
                                    '{"segment_id":"seg_1","runs":[{"run_id":"run_1","text":"研究经历"}]},'
                                    '{"segment_id":"seg_2","runs":[{"run_id":"run_1","text":"我做过医学 NLP 与信息抽取项目。"}]},'
                                    '{"segment_id":"seg_3","runs":[{"run_id":"run_1","text":"我近期关注到您在 "},'
                                    '{"run_id":"run_2","text":"[[PH_1]]"},'
                                    '{"run_id":"run_3","text":" 方向的研究。"}]}'
                                    '],"suggested_material_ids":[7]}'
                                ),
                            },
                        },
                    ],
                },
            ),
        ]

        with patch(
            "app.services.llm_runtime.httpx.AsyncClient",
            side_effect=lambda *args, **kwargs: _FakeAsyncClient(responses, calls),
        ):
            result = await generate_draft_content(
                identity=identity,
                primary_material=primary_material,
                llm_profile=profile,
                professor=professor,
                available_materials=[primary_material],
                custom_subject="申请交流",
                custom_body="研究经历\n我做过信息抽取项目。\n我对您的 {{research_direction}} 方向很感兴趣。",
                custom_body_html=(
                    '<table style="border-collapse:collapse"><tbody><tr>'
                    '<td style="border:1px solid #ccc">研究经历</td>'
                    '<td style="font-size:11pt">我做过信息抽取项目。</td>'
                    '</tr></tbody></table>'
                    '<p>我对您的 <strong>{{research_direction}}</strong> 方向很感兴趣。</p>'
                ),
            )

        self.assertIn("<table", result.result.body_html)
        self.assertIn('style="font-size:11pt"', result.result.body_html)
        self.assertIn("<strong>{{research_direction}}</strong>", result.result.body_html)
        self.assertEqual(result.result.suggested_material_ids, [7])
```

- [ ] **步骤 4：运行最终后端相关测试**

运行：

```powershell
rtk uv run python -m unittest test.test_template_run_rewrite
rtk uv run python -m unittest test.test_rich_text
rtk uv run python -m unittest test.test_llm_runtime
rtk uv run python -m unittest test.test_batch_draft_generation_runtime
```

预期：全部 `OK`。

- [ ] **步骤 5：Commit**

```powershell
rtk git add backend/test/test_batch_draft_generation_runtime.py backend/test/test_llm_runtime.py backend/test/test_template_run_rewrite.py
rtk git commit -m "test(backend): cover run rewrite draft generation"
```

## 任务 7：最终检查

**文件：**
- 检查：`backend/app/services/template_run_rewrite.py`
- 检查：`backend/app/services/llm_runtime.py`
- 检查：`backend/app/api/workspace_support.py`
- 检查：相关测试文件

- [ ] **步骤 1：运行后端目标测试集**

运行：

```powershell
rtk uv run python -m unittest test.test_template_run_rewrite
rtk uv run python -m unittest test.test_rich_text
rtk uv run python -m unittest test.test_llm_runtime
rtk uv run python -m unittest test.test_batch_draft_generation_runtime
```

预期：全部 `OK`。

- [ ] **步骤 2：运行工作区状态检查**

运行：

```powershell
rtk git status --short
```

预期：只看到本计划相关文件和用户已存在的无关改动。不要回滚用户无关改动。

- [ ] **步骤 3：检查 prompt 泄漏**

运行：

```powershell
rtk rg -n "套磁信模板正文 HTML|custom_body_html or \"无\"|<p style=" backend/app/services/llm_runtime.py backend/test/test_llm_runtime.py
```

预期：生产 prompt 路径不再包含旧的“套磁信模板正文 HTML”字段；测试中可以包含断言用字符串。

- [ ] **步骤 4：Commit 最终清理**

如果步骤 1-3 发现并修正了遗漏，提交：

```powershell
rtk git add backend/app/services backend/app/api backend/test
rtk git commit -m "chore(backend): finalize run rewrite integration"
```

如果没有新增修改，不创建空 commit。
