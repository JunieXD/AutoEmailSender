# 锚点化模板改写实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将工作区 AI 草稿生成从碎片化 run 改写切换为锚点化 segment 改写，保留关键格式并减少边界标点、重复词和断句问题。

**架构：** 新增 `template_anchor_rewrite.py`，负责把 `TemplateRunDocument` 转成带锚点的连续 segment、校验模型返回、并把返回文本写回原 HTML。`llm_runtime.py` 只负责构建锚点化 prompt、调用模型、解析受控 JSON，并复用新模块写回 HTML。

**技术栈：** Python 3.12、FastAPI 服务层、Pydantic、BeautifulSoup、`unittest`、现有 `uv` 测试命令。

---

## 文件结构

- 创建：`backend/app/services/template_anchor_rewrite.py`
  - 定义锚点化数据结构。
  - 从 `TemplateRunDocument` 构造锚点化 segments。
  - 校验模型返回的锚点集合和顺序。
  - 将模型返回的锚点化文本写回原始 HTML。
- 创建：`backend/test/test_template_anchor_rewrite.py`
  - 覆盖锚点构造、写回、格式保留和失败校验。
- 修改：`backend/app/services/llm_runtime.py`
  - 新增锚点化 prompt 和 result schema。
  - 将模板草稿生成路径切换到锚点化改写。
- 修改：`backend/test/test_llm_runtime.py`
  - 覆盖 LLM runtime 使用锚点化 prompt 和写回结果。

## 任务 1：锚点化数据结构与构造

**文件：**
- 创建：`backend/test/test_template_anchor_rewrite.py`
- 创建：`backend/app/services/template_anchor_rewrite.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/test/test_template_anchor_rewrite.py` 中创建测试文件：

```python
from __future__ import annotations

import unittest

from app.services.template_run_rewrite import build_template_run_document
from app.services.template_anchor_rewrite import build_anchored_template_document


class TemplateAnchorRewriteTests(unittest.TestCase):
    def test_build_anchored_document_groups_strong_runs_as_anchor(self) -> None:
        document = build_template_run_document(
            "<p>我是王俊杰，<strong>以专业第一的成绩获得</strong>"
            "<strong>了</strong><strong>推免资格</strong>。现在联系您或许有些晚了，附件中是我的简历。</p>",
        )

        anchored = build_anchored_template_document(document)
        segment = anchored.segments[0]

        self.assertEqual(
            segment.rewrite_text,
            "我是王俊杰，[[A1]]。现在联系您或许有些晚了，附件中是我的简历。",
        )
        self.assertEqual(len(segment.anchors), 1)
        self.assertEqual(segment.anchors[0].anchor_id, "A1")
        self.assertEqual(segment.anchors[0].text, "以专业第一的成绩获得了推免资格")
        self.assertEqual(segment.anchors[0].source_runs, ["run_2", "run_3", "run_4"])
        self.assertEqual(segment.anchors[0].marks, ["strong"])

    def test_build_anchored_document_keeps_placeholder_as_anchor(self) -> None:
        document = build_template_run_document("<p>尊敬的{{name}}教授：</p>")

        anchored = build_anchored_template_document(document)
        segment = anchored.segments[0]

        self.assertEqual(segment.rewrite_text, "尊敬的[[A1]]教授：")
        self.assertEqual(segment.anchors[0].text, "[[PH_1]]")
        self.assertEqual(segment.anchors[0].locked_placeholders, [{"token": "[[PH_1]]", "original": "{{name}}"}])
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_template_anchor_rewrite
```

预期：FAIL，报错包含 `ModuleNotFoundError: No module named 'app.services.template_anchor_rewrite'`。

- [ ] **步骤 3：编写最少实现代码**

创建 `backend/app/services/template_anchor_rewrite.py`：

```python
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.template_run_rewrite import TemplateRunDocument, TemplateRun, TemplateSegment


SIGNIFICANT_MARKS = {"strong", "emphasis", "underline", "link", "placeholder"}


@dataclass(slots=True)
class TemplateAnchor:
    anchor_id: str
    text: str
    segment_id: str
    source_runs: list[str]
    marks: list[str] = field(default_factory=list)
    locked_placeholders: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class AnchoredTemplateSegment:
    segment_id: str
    role: str
    rewrite_text: str
    anchors: list[TemplateAnchor]


@dataclass(slots=True)
class AnchoredTemplateDocument:
    segments: list[AnchoredTemplateSegment]


def build_anchored_template_document(document: TemplateRunDocument) -> AnchoredTemplateDocument:
    segments = [_build_anchored_segment(segment) for segment in document.segments]
    return AnchoredTemplateDocument(segments=segments)


def _build_anchored_segment(segment: TemplateSegment) -> AnchoredTemplateSegment:
    anchors: list[TemplateAnchor] = []
    parts: list[str] = []
    index = 0

    while index < len(segment.runs):
        run = segment.runs[index]
        if not _is_anchor_run(run):
            parts.append(run.text)
            index += 1
            continue

        group = [run]
        index += 1
        while index < len(segment.runs) and _can_merge_anchor_run(group[-1], segment.runs[index]):
            group.append(segment.runs[index])
            index += 1

        anchor_id = f"A{len(anchors) + 1}"
        anchor = TemplateAnchor(
            anchor_id=anchor_id,
            text="".join(item.text for item in group),
            segment_id=segment.segment_id,
            source_runs=[item.run_id for item in group],
            marks=list(group[0].marks),
            locked_placeholders=[
                placeholder
                for item in group
                for placeholder in item.locked_placeholders
            ],
        )
        anchors.append(anchor)
        parts.append(f"[[{anchor_id}]]")

    return AnchoredTemplateSegment(
        segment_id=segment.segment_id,
        role=segment.role,
        rewrite_text="".join(parts),
        anchors=anchors,
    )


def _is_anchor_run(run: TemplateRun) -> bool:
    return bool(set(run.marks) & SIGNIFICANT_MARKS)


def _can_merge_anchor_run(previous: TemplateRun, current: TemplateRun) -> bool:
    return previous.marks == current.marks and previous.locked_placeholders == current.locked_placeholders == []
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test.test_template_anchor_rewrite
```

预期：2 条测试通过，输出 `OK`。

- [ ] **步骤 5：Commit**

```powershell
rtk git add backend/app/services/template_anchor_rewrite.py backend/test/test_template_anchor_rewrite.py
rtk git commit -m "feat(草稿生成): 添加模板锚点化结构"
```

## 任务 2：锚点化写回与校验

**文件：**
- 修改：`backend/test/test_template_anchor_rewrite.py`
- 修改：`backend/app/services/template_anchor_rewrite.py`

- [ ] **步骤 1：编写失败的测试**

向 `TemplateAnchorRewriteTests` 增加测试：

```python
    def test_apply_anchored_replacements_preserves_strong_anchor(self) -> None:
        document = build_template_run_document(
            "<p>我是王俊杰，<strong>以专业第一的成绩获得</strong>"
            "<strong>了</strong><strong>推免资格</strong>。现在联系您或许有些晚了，附件中是我的简历。</p>",
        )
        anchored = build_anchored_template_document(document)

        rendered = apply_anchored_template_replacements(
            document,
            anchored,
            [
                {
                    "segment_id": "seg_1",
                    "text": "我是王俊杰，[[A1]]。冒昧来信咨询，不知老师今年是否还有硕士招生名额？附件中是我的简历。",
                },
            ],
        )

        self.assertIn("<strong>", rendered.html)
        self.assertIn("以专业第一的成绩获得", rendered.html)
        self.assertIn("推免资格", rendered.html)
        self.assertIn("推免资格。冒昧来信咨询", rendered.text)
        self.assertNotIn("推免资格冒昧", rendered.text)

    def test_apply_anchored_replacements_rejects_missing_anchor(self) -> None:
        document = build_template_run_document("<p>尊敬的{{name}}教授：</p>")
        anchored = build_anchored_template_document(document)

        with self.assertRaisesRegex(ValueError, "锚点缺失: seg_1/A1"):
            apply_anchored_template_replacements(
                document,
                anchored,
                [{"segment_id": "seg_1", "text": "尊敬的教授："}],
            )

    def test_apply_anchored_replacements_rejects_reordered_anchor(self) -> None:
        document = build_template_run_document("<p><strong>A</strong> 普通 <em>B</em></p>")
        anchored = build_anchored_template_document(document)

        with self.assertRaisesRegex(ValueError, "锚点顺序错误: seg_1"):
            apply_anchored_template_replacements(
                document,
                anchored,
                [{"segment_id": "seg_1", "text": "[[A2]] 普通 [[A1]]"}],
            )
```

同步补充 import：

```python
from app.services.template_anchor_rewrite import (
    apply_anchored_template_replacements,
    build_anchored_template_document,
)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_template_anchor_rewrite
```

预期：FAIL，报错包含 `ImportError` 或 `AttributeError`，说明 `apply_anchored_template_replacements` 尚未实现。

- [ ] **步骤 3：编写最少实现代码**

在 `backend/app/services/template_anchor_rewrite.py` 中追加：

```python
import copy
import re
from bs4 import BeautifulSoup, NavigableString

from app.services.rich_text import RichTextRenderResult, normalize_email_html


ANCHOR_TOKEN_PATTERN = re.compile(r"\[\[A\d+\]\]")


def apply_anchored_template_replacements(
    document: TemplateRunDocument,
    anchored_document: AnchoredTemplateDocument,
    replacements: list[dict[str, object]],
) -> RichTextRenderResult:
    anchored_map = {segment.segment_id: segment for segment in anchored_document.segments}
    replacement_map = _validate_replacements(anchored_map, replacements)

    for segment in document.segments:
        anchored_segment = anchored_map.get(segment.segment_id)
        if anchored_segment is None or segment.segment_id not in replacement_map:
            continue
        _replace_segment_contents(document, segment, anchored_segment, replacement_map[segment.segment_id])

    return normalize_email_html(str(document.soup))


def _validate_replacements(
    anchored_map: dict[str, AnchoredTemplateSegment],
    replacements: list[dict[str, object]],
) -> dict[str, str]:
    if not replacements:
        raise ValueError("模型未返回可用改写内容（replacements 为空）")

    replacement_map: dict[str, str] = {}
    for item in replacements:
        if not isinstance(item, dict):
            raise ValueError("模型未返回可用改写内容（无效 replacement 项）")
        segment_id = item.get("segment_id")
        text = item.get("text")
        if not isinstance(segment_id, str) or not isinstance(text, str):
            raise ValueError("模型未返回可用改写内容（缺失或非法 segment_id/text）")
        anchored_segment = anchored_map.get(segment_id)
        if anchored_segment is None:
            raise ValueError(f"模型未返回可用改写内容（无效 segment_id: {segment_id}）")
        _validate_anchor_tokens(anchored_segment, text)
        replacement_map[segment_id] = text
    return replacement_map


def _validate_anchor_tokens(segment: AnchoredTemplateSegment, text: str) -> None:
    expected = [anchor.anchor_id for anchor in segment.anchors]
    actual = [token[2:-2] for token in ANCHOR_TOKEN_PATTERN.findall(text)]
    for anchor_id in expected:
        if anchor_id not in actual:
            raise ValueError(f"锚点缺失: {segment.segment_id}/{anchor_id}")
    unknown = [anchor_id for anchor_id in actual if anchor_id not in expected]
    if unknown:
        raise ValueError(f"未知锚点: {segment.segment_id}/{unknown[0]}")
    expected_positions = [expected.index(anchor_id) for anchor_id in actual]
    if expected_positions != sorted(expected_positions):
        raise ValueError(f"锚点顺序错误: {segment.segment_id}")


def _replace_segment_contents(
    document: TemplateRunDocument,
    segment: TemplateSegment,
    anchored_segment: AnchoredTemplateSegment,
    text: str,
) -> None:
    container = _find_segment_container(document.nodes[segment.runs[0].node_index])
    anchor_html = _anchor_html_map(document, segment, anchored_segment)
    fragment_html = _render_replacement_fragment(text, anchor_html)
    fragment = BeautifulSoup(fragment_html, "html.parser")
    container.clear()
    for child in list(fragment.contents):
        container.append(copy.copy(child))


def _find_segment_container(node: NavigableString):
    segment_names = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th"}
    for parent in node.parents:
        if getattr(parent, "name", None) in segment_names:
            return parent
    raise ValueError("无法定位模板段落容器")


def _anchor_html_map(
    document: TemplateRunDocument,
    segment: TemplateSegment,
    anchored_segment: AnchoredTemplateSegment,
) -> dict[str, str]:
    run_map = {run.run_id: run for run in segment.runs}
    html_by_anchor: dict[str, str] = {}
    for anchor in anchored_segment.anchors:
        html_parts: list[str] = []
        for run_id in anchor.source_runs:
            run = run_map[run_id]
            node = document.nodes[run.node_index]
            parent = node.parent
            html_parts.append(str(parent) if parent is not None else str(node))
        html_by_anchor[anchor.anchor_id] = "".join(html_parts)
    return html_by_anchor


def _render_replacement_fragment(text: str, anchor_html: dict[str, str]) -> str:
    parts: list[str] = []
    cursor = 0
    for match in ANCHOR_TOKEN_PATTERN.finditer(text):
        parts.append(str(BeautifulSoup("", "html.parser").new_string(text[cursor:match.start()])))
        anchor_id = match.group(0)[2:-2]
        parts.append(anchor_html[anchor_id])
        cursor = match.end()
    parts.append(str(BeautifulSoup("", "html.parser").new_string(text[cursor:])))
    return "".join(parts)
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test.test_template_anchor_rewrite
```

预期：5 条测试通过，输出 `OK`。

- [ ] **步骤 5：Commit**

```powershell
rtk git add backend/app/services/template_anchor_rewrite.py backend/test/test_template_anchor_rewrite.py
rtk git commit -m "feat(草稿生成): 实现锚点化模板写回"
```

## 任务 3：锚点化 prompt 与模型结果 schema

**文件：**
- 修改：`backend/app/services/llm_runtime.py`
- 修改：`backend/test/test_llm_runtime.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/test/test_llm_runtime.py` 中新增测试：

```python
    def test_build_template_anchor_rewrite_prompt_sends_rewrite_segments(self) -> None:
        from app.models import IdentityMaterial, IdentityProfile, Professor
        from app.services.template_run_rewrite import build_template_run_document
        from app.services.template_anchor_rewrite import build_anchored_template_document
        from app.services.llm_runtime import build_template_anchor_rewrite_prompt

        identity = IdentityProfile(
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
            id=12,
            identity_id=1,
            display_name="简历",
            file_path="data/materials/resume.txt",
            original_filename="resume.txt",
            material_type="resume",
            extracted_text="我做过信息抽取与智能体相关研究。",
        )
        professor = Professor(
            name="李老师",
            email="prof@example.edu",
            title="Professor",
            university="Example University",
            school="Computer Science",
            research_direction="Information Extraction",
        )
        document = build_template_run_document("<p>老师您好，我来自 <strong>Example University</strong>。</p>")
        anchored_document = build_anchored_template_document(document)

        prompt = build_template_anchor_rewrite_prompt(
            identity=identity,
            primary_material=primary_material,
            professor=professor,
            available_materials=[primary_material],
            subject_template="申请与{{name}}老师交流",
            anchored_document=anchored_document,
            current_match=None,
            rewrite_preferences=None,
        )

        payload = json.loads(prompt)
        self.assertEqual(payload["task"], "rewrite_email_template_anchored_segments")
        self.assertEqual(payload["rewrite_segments"][0]["rewrite_text"], "老师您好，我来自 [[A1]]。")
        self.assertEqual(payload["rewrite_segments"][0]["anchors"][0]["text"], "Example University")
        self.assertEqual(
            payload["response_schema"]["replacements"][0],
            {"segment_id": "seg_1", "text": "改写后的锚点化 segment 文本"},
        )
        self.assertIn("锚点 token 必须原样保留", payload["instructions"])
        self.assertNotIn("<strong>Example University</strong>", prompt)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_build_template_anchor_rewrite_prompt_sends_rewrite_segments
```

预期：FAIL，报错包含 `ImportError`，说明 `build_template_anchor_rewrite_prompt` 尚未实现。

- [ ] **步骤 3：编写最少实现代码**

在 `backend/app/services/llm_runtime.py` 中：

1. 增加 import：

```python
from app.services.template_anchor_rewrite import AnchoredTemplateDocument
```

2. 增加 Pydantic schema：

```python
class TemplateAnchorSegmentReplacement(BaseModel):
    segment_id: str
    text: str


class TemplateAnchorRewriteResult(BaseModel):
    subject: str
    replacements: list[TemplateAnchorSegmentReplacement] = Field(default_factory=list)
    suggested_material_ids: list[int] = Field(default_factory=list)
```

3. 将 `StructuredResultT` 加入 `TemplateAnchorRewriteResult`，并在 `parse_structured_result()` 中增加：

```python
    if isinstance(result, TemplateAnchorRewriteResult):
        result.subject = _normalize_text_field(result.subject, "subject")
        result.suggested_material_ids = _normalize_integer_list(result.suggested_material_ids)
        return result  # type: ignore[return-value]
```

4. 新增 system prompt：

```python
SYSTEM_TEMPLATE_ANCHOR_REWRITE_PROMPT = dedent(
    """
    你是研究生套磁邮件改写助理。你必须只输出 JSON。
    你不能输出 HTML、Markdown 或解释。
    你只能改写 rewrite_segments 中的 text。
    你不能新增、删除、合并、拆分或重排 segment。
    锚点 token 例如 [[A1]] 必须原样保留，不能改写、删除、新增或移动顺序。
    占位符已经被锚点保护，不能通过改写绕过锚点。

    JSON 字段必须包含：
    - subject: 邮件主题
    - replacements: segment 替换数组
    - suggested_material_ids: 整数数组，只能从可选材料 ID 中选择
    replacements 中每项只能包含 segment_id 和 text。
    """
).strip()
```

5. 新增 prompt 构造函数：

```python
def build_template_anchor_rewrite_prompt(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    subject_template: str | None,
    anchored_document: AnchoredTemplateDocument,
    current_match: MatchEvaluationResult | None,
    rewrite_preferences: DraftRewritePreferences | None,
) -> str:
    primary_material_text = (primary_material.extracted_text if primary_material else "") or ""
    if len(primary_material_text) > 5000:
        primary_material_text = f"{primary_material_text[:5000]}\n...(已截断)"

    payload = {
        "task": "rewrite_email_template_anchored_segments",
        "context": {
            "identity": {
                "name": _format_nullable(identity.name),
                "email_address": _format_nullable(identity.email_address),
                "default_language": _format_nullable(identity.default_language),
                "match_threshold": identity.match_threshold,
            },
            "professor": {
                "name": _format_nullable(professor.name),
                "email": _format_nullable(professor.email),
                "title": _format_nullable(professor.title),
                "university": _format_nullable(professor.university),
                "school": _format_nullable(professor.school),
                "department": _format_nullable(professor.department),
                "research_direction": _format_nullable(professor.research_direction),
                "profile_url": _format_nullable(professor.profile_url),
                "recent_papers": professor.recent_papers or [],
            },
            "student": {
                "primary_material": {
                    "id": primary_material.id if primary_material else None,
                    "name": _format_nullable(primary_material.display_name if primary_material else None),
                    "type": _format_nullable(primary_material.material_type if primary_material else None),
                    "extracted_text": primary_material_text,
                },
            },
            "current_match": current_match.model_dump() if current_match is not None else None,
            "rewrite_preferences": asdict(rewrite_preferences or DraftRewritePreferences()),
        },
        "subject_template": subject_template or "",
        "rewrite_segments": [
            {
                "segment_id": segment.segment_id,
                "role": segment.role,
                "rewrite_text": segment.rewrite_text,
                "anchors": [
                    {
                        "anchor_id": anchor.anchor_id,
                        "text": anchor.text,
                        "marks": anchor.marks,
                        "locked_placeholders": anchor.locked_placeholders,
                    }
                    for anchor in segment.anchors
                ],
            }
            for segment in anchored_document.segments
        ],
        "response_schema": {
            "subject": "邮件主题",
            "replacements": [
                {"segment_id": "seg_1", "text": "改写后的锚点化 segment 文本"},
            ],
            "suggested_material_ids": [material.id for material in available_materials[:1]],
        },
        "available_materials": [
            {
                "id": material.id,
                "name": _format_nullable(material.display_name),
                "type": _format_nullable(material.material_type),
            }
            for material in available_materials
        ],
        "instructions": [
            "只返回 JSON 对象。",
            "replacements 只能引用 rewrite_segments 中已有的 segment_id。",
            "每个 replacement 只允许包含 segment_id 和 text。",
            "锚点 token 必须原样保留，例如 [[A1]]。",
            "锚点 token 的顺序必须与输入 rewrite_text 一致。",
            "不要新增未知锚点 token。",
            "不要返回 HTML 或完整正文。",
            "不需要改写的 segment 不要放入 replacements。",
            "suggested_material_ids 只能选择 available_materials 中存在的 id。",
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_build_template_anchor_rewrite_prompt_sends_rewrite_segments
```

预期：测试通过，输出 `OK`。

- [ ] **步骤 5：Commit**

```powershell
rtk git add backend/app/services/llm_runtime.py backend/test/test_llm_runtime.py
rtk git commit -m "feat(草稿生成): 添加锚点化改写提示词"
```

## 任务 4：切换草稿生成模板路径

**文件：**
- 修改：`backend/app/services/llm_runtime.py`
- 修改：`backend/test/test_llm_runtime.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/test/test_llm_runtime.py` 增加异步测试：

```python
    async def test_generate_draft_content_uses_anchored_rewrite_and_preserves_strong_anchor(self) -> None:
        from app.models import IdentityMaterial, IdentityProfile, LLMProfile, Professor

        identity = IdentityProfile(
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
            id=12,
            identity_id=1,
            display_name="简历",
            file_path="data/materials/resume.txt",
            original_filename="resume.txt",
            material_type="resume",
            extracted_text="我做过信息抽取与智能体相关研究。",
        )
        professor = Professor(
            name="李老师",
            email="prof@example.edu",
            title="Professor",
            university="Example University",
            school="Computer Science",
            research_direction="Information Extraction",
        )
        llm_profile = LLMProfile(
            provider="openai",
            model_name="test-model",
            api_base_url="https://api.example.com/v1",
            api_key="secret",
            max_tokens=1000,
            temperature=0,
        )

        raw = json.dumps(
            {
                "subject": "申请与李老师交流",
                "replacements": [
                    {
                        "segment_id": "seg_1",
                        "text": "我是王俊杰，[[A1]]。冒昧来信咨询，不知老师今年是否还有硕士招生名额？附件中是我的简历。",
                    },
                ],
                "suggested_material_ids": [12],
            },
            ensure_ascii=False,
        )

        with patch(
            "app.services.llm_runtime.request_chat_completion",
            return_value=ChatCompletionResult(content=raw),
        ) as request_mock:
            generated = await generate_draft_content(
                identity=identity,
                primary_material=primary_material,
                llm_profile=llm_profile,
                professor=professor,
                available_materials=[primary_material],
                custom_subject="申请与{{name}}老师交流",
                custom_body_html=(
                    "<p>我是王俊杰，<strong>以专业第一的成绩获得</strong>"
                    "<strong>了</strong><strong>推免资格</strong>。现在联系您或许有些晚了，附件中是我的简历。</p>"
                ),
            )

        payload = request_mock.call_args.args[1]
        self.assertEqual(payload["messages"][0]["content"], SYSTEM_TEMPLATE_ANCHOR_REWRITE_PROMPT)
        self.assertIn("rewrite_segments", payload["messages"][1]["content"])
        self.assertIn("推免资格。冒昧来信咨询", generated.result.body_text)
        self.assertNotIn("推免资格冒昧", generated.result.body_text)
        self.assertIn("<strong>", generated.result.body_html)
```

同步在 import 列表中加入：

```python
    ChatCompletionResult,
    SYSTEM_TEMPLATE_ANCHOR_REWRITE_PROMPT,
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_generate_draft_content_uses_anchored_rewrite_and_preserves_strong_anchor
```

预期：FAIL，断言显示当前仍使用 `SYSTEM_TEMPLATE_RUN_REWRITE_PROMPT` 或解析 `runs` schema。

- [ ] **步骤 3：编写最少实现代码**

在 `backend/app/services/llm_runtime.py` 中：

1. 修改 import：

```python
from app.services.template_anchor_rewrite import (
    apply_anchored_template_replacements,
    build_anchored_template_document,
)
```

2. 将 `generate_draft_content()` 的 `if template_html:` 分支替换为锚点化路径：

```python
    if template_html:
        template_document = build_template_run_document(template_html)
        anchored_document = build_anchored_template_document(template_document)
        prompt = build_template_anchor_rewrite_prompt(
            identity=identity,
            primary_material=primary_material,
            professor=professor,
            available_materials=available_materials,
            subject_template=custom_subject,
            anchored_document=anchored_document,
            current_match=current_match,
            rewrite_preferences=rewrite_preferences,
        )
        completion = await request_chat_completion(
            llm_profile,
            {
                "model": llm_profile.model_name,
                "messages": [
                    {
                        "role": "system",
                        "content": SYSTEM_TEMPLATE_ANCHOR_REWRITE_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                "temperature": llm_profile.temperature if llm_profile.temperature is not None else DEFAULT_LLM_TEMPERATURE,
                "max_tokens": max_tokens or DEFAULT_LLM_MAX_TOKENS,
            },
        )
        rewrite_result = parse_structured_result(completion.content, TemplateAnchorRewriteResult)
        try:
            rendered = apply_anchored_template_replacements(
                template_document,
                anchored_document,
                [item.model_dump() for item in rewrite_result.replacements],
            )
        except ValueError as exc:
            raise LLMRuntimeError(str(exc)) from exc
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

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_generate_draft_content_uses_anchored_rewrite_and_preserves_strong_anchor
```

预期：测试通过，输出 `OK`。

- [ ] **步骤 5：运行相关测试**

运行：

```powershell
rtk uv run python -m unittest test.test_template_anchor_rewrite test.test_llm_runtime
```

预期：全部通过，输出 `OK`。

- [ ] **步骤 6：Commit**

```powershell
rtk git add backend/app/services/llm_runtime.py backend/test/test_llm_runtime.py
rtk git commit -m "feat(草稿生成): 启用锚点化模板改写"
```

## 任务 5：回归验证与边界样例

**文件：**
- 修改：`backend/test/test_template_anchor_rewrite.py`

- [ ] **步骤 1：编写失败的边界测试**

在 `TemplateAnchorRewriteTests` 中增加两个回归测试：

```python
    def test_apply_anchored_replacements_avoids_comma_boundary_artifact(self) -> None:
        document = build_template_run_document(
            "<p><span>以下是我的个人介绍和未来规划</span><span>，</span><span>附件中是我的简历。</span></p>",
        )
        anchored = build_anchored_template_document(document)

        rendered = apply_anchored_template_replacements(
            document,
            anchored,
            [{"segment_id": "seg_1", "text": "以下是我的个人情况与未来规划，附件中是我的简历。"}],
        )

        self.assertIn("未来规划，附件中", rendered.text)
        self.assertNotIn("未来规划。 ，", rendered.text)
        self.assertNotIn("未来规划，，", rendered.text)

    def test_apply_anchored_replacements_avoids_split_project_duplicate(self) -> None:
        document = build_template_run_document(
            "<p><span>④多模态谣言检测模型的对抗攻击与数据增强研究</span>"
            "<span>（</span><span>科研</span><span>项目）：基于文本风格改写方法。</span></p>",
        )
        anchored = build_anchored_template_document(document)

        rendered = apply_anchored_template_replacements(
            document,
            anchored,
            [{"segment_id": "seg_1", "text": "④多模态谣言检测模型的对抗攻击与数据增强研究（科研项目）：基于文本风格改写方法。"}],
        )

        self.assertIn("（科研项目）：", rendered.text)
        self.assertNotIn("科研科研项目", rendered.text)
```

- [ ] **步骤 2：运行测试验证失败或通过原因**

运行：

```powershell
rtk uv run python -m unittest test.test_template_anchor_rewrite
```

预期：如果任务 2 的写回策略已正确覆盖普通碎片，测试通过；如果仍按旧 run 拼接，测试失败并暴露具体边界。

- [ ] **步骤 3：如失败则修正普通文本写回**

若失败，修改 `template_anchor_rewrite.py` 的 `_replace_segment_contents()`，确保无锚点 segment 会清空容器后写入一段连续普通文本：

```python
def _replace_segment_contents(
    document: TemplateRunDocument,
    segment: TemplateSegment,
    anchored_segment: AnchoredTemplateSegment,
    text: str,
) -> None:
    container = _find_segment_container(document.nodes[segment.runs[0].node_index])
    anchor_html = _anchor_html_map(document, segment, anchored_segment)
    fragment_html = _render_replacement_fragment(text, anchor_html)
    fragment = BeautifulSoup(fragment_html, "html.parser")
    container.clear()
    for child in list(fragment.contents):
        container.append(copy.copy(child))
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test.test_template_anchor_rewrite
```

预期：全部通过，输出 `OK`。

- [ ] **步骤 5：运行后端相关测试**

运行：

```powershell
rtk uv run python -m unittest test.test_template_anchor_rewrite test.test_llm_runtime
```

预期：全部通过，输出 `OK`。

- [ ] **步骤 6：Commit**

```powershell
rtk git add backend/test/test_template_anchor_rewrite.py backend/app/services/template_anchor_rewrite.py
rtk git commit -m "test(草稿生成): 覆盖模板改写边界回归"
```

## 最终验证

- [ ] **步骤 1：运行核心测试**

```powershell
rtk uv run python -m unittest test.test_template_anchor_rewrite test.test_llm_runtime
```

预期：全部通过，输出 `OK`。

- [ ] **步骤 2：运行更宽范围后端测试**

```powershell
rtk uv run python -m unittest discover test
```

预期：全部通过；如果测试集执行时间超过 120 秒，记录超时事实和已经通过的核心测试，不把超时说成通过。

- [ ] **步骤 3：检查工作区**

```powershell
rtk git status --short
```

预期：没有未提交变更，或只剩用户明确保留的无关变更。
