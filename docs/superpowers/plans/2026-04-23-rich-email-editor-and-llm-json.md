# 富文本邮件编辑与 LLM 结构化草稿实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 统一个人页模板、测试写信页和正式工作区写信区的富文本邮件编辑体验，并让 LLM 通过受控 JSON 生成安全富文本草稿。

**架构：** 后端新增 `rich_text` 服务作为内容内核，负责受控富文本 JSON 校验、HTML 渲染和纯文本派生；LLM 仍对外返回现有 `body_html` / `body_text` 字段，但内部先解析富文本 JSON。前端新增共用 `RichEmailEditor`，三个入口都使用它编辑 HTML，同时向后端提交派生纯文本。

**技术栈：** FastAPI、Pydantic、SQLAlchemy、BeautifulSoup、React 19、Vite、Testing Library、Vitest、DOMPurify、现有 uv / npm 脚本。

---

## 文件结构

- 创建：`backend/app/services/rich_text.py`
  - 定义受控富文本 JSON schema、渲染 HTML、派生纯文本、清洗 HTML 的后端内核。
- 创建：`backend/test/test_rich_text.py`
  - 覆盖富文本 JSON 渲染、安全链接过滤、HTML 到纯文本派生。
- 修改：`backend/app/services/llm_runtime.py`
  - 将 LLM 生成草稿输出从直接 `body_html` 调整为 `rich_body` JSON，再渲染为 `body_html` / `body_text`。
- 修改：`backend/app/services/outreach_templates.py`
  - 模板导入后继续输出 `body_html` / `body_text`，但通过 `rich_text` 统一清洗和派生纯文本。
- 修改：`backend/app/services/task_runtime.py`
  - 保存和发送正式草稿时使用 `rich_text` 规范化 HTML 与纯文本。
- 修改：`backend/app/services/test_compose_runtime.py`
  - 保存和发送测试草稿时使用 `rich_text` 规范化 HTML 与纯文本。
- 创建：`frontend/src/lib/richEmail.ts`
  - 提供前端 HTML 清洗、HTML 转纯文本、编辑器默认 HTML 生成工具。
- 创建：`frontend/src/components/molecules/RichEmailEditor.tsx`
  - 共用富文本邮件编辑器，输出 `html` 和 `text`。
- 创建：`frontend/test/richEmail.test.ts`
  - 覆盖前端 HTML 转纯文本和默认 HTML 生成。
- 创建：`frontend/test/RichEmailEditor.test.tsx`
  - 覆盖富文本编辑器加粗、列表、链接、输出 HTML 和纯文本。
- 修改：`frontend/src/pages/ProfilePage.tsx`
  - 默认模板弹窗删除纯文本正文和 HTML 源码双输入，改用 `RichEmailEditor`。
- 修改：`frontend/test/ProfilePageOnboarding.test.tsx`
  - 更新个人页模板文案和富文本模板导入断言。
- 修改：`frontend/src/pages/TestComposePage.tsx`
  - 邮件正文使用 `RichEmailEditor`，保存和发送提交 `body_html` 与 `body_text`。
- 修改：`frontend/test/TestComposePage.test.tsx`
  - 覆盖测试写信页加载 HTML 草稿、编辑富文本、保存/发送 payload。
- 修改：`frontend/src/components/organisms/WorkspaceComposerDock.tsx`
  - 正式写信区使用 `RichEmailEditor`，删除“手动改正文会切回普通文本”的提示。
- 修改：`frontend/src/pages/WorkspacePage.tsx`
  - 将正文状态改为 `contentHtml` 主导，`content` 由编辑器派生。
- 修改：`frontend/test/WorkspaceComposerDockCopy.test.tsx`
  - 更新正式写信区富文本编辑器断言。
- 修改：`frontend/test/WorkspacePageNextStep.test.tsx`
  - 更新 HTML-only 草稿发送 payload 断言。

## 任务 1：后端富文本内核

**文件：**
- 创建：`backend/test/test_rich_text.py`
- 创建：`backend/app/services/rich_text.py`

- [ ] **步骤 1：编写失败的富文本渲染测试**

在 `backend/test/test_rich_text.py` 写入：

```python
import unittest

from app.services.rich_text import render_rich_text_document


class RichTextRenderingTest(unittest.TestCase):
    def test_renders_rich_text_json_to_safe_html_and_text(self) -> None:
        result = render_rich_text_document(
            {
                "type": "doc",
                "blocks": [
                    {
                        "type": "paragraph",
                        "children": [
                            {"type": "text", "text": "王老师您好，"},
                            {"type": "strong", "children": [{"type": "text", "text": "我很关注您的研究"}]},
                        ],
                    },
                    {
                        "type": "bullet_list",
                        "items": [
                            [{"type": "text", "text": "信息抽取方向"}],
                            [{"type": "emphasis", "children": [{"type": "text", "text": "医学 NLP 应用"}]}],
                        ],
                    },
                ],
            }
        )

        self.assertIn("<p>王老师您好，<strong>我很关注您的研究</strong></p>", result.html)
        self.assertIn("<ul>", result.html)
        self.assertEqual(result.text, "王老师您好，我很关注您的研究\n- 信息抽取方向\n- 医学 NLP 应用")

    def test_rejects_unsafe_link_protocol(self) -> None:
        with self.assertRaisesRegex(ValueError, "不支持的链接协议"):
            render_rich_text_document(
                {
                    "type": "doc",
                    "blocks": [
                        {
                            "type": "paragraph",
                            "children": [
                                {
                                    "type": "link",
                                    "href": "javascript:alert(1)",
                                    "children": [{"type": "text", "text": "危险链接"}],
                                }
                            ],
                        }
                    ],
                }
            )
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest backend.test.test_rich_text`

预期：FAIL，报错包含 `ModuleNotFoundError: No module named 'app.services.rich_text'`。

- [ ] **步骤 3：实现后端富文本渲染内核**

创建 `backend/app/services/rich_text.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

ALLOWED_LINK_SCHEMES = {"http", "https", "mailto"}


@dataclass(frozen=True)
class RichTextRenderResult:
    html: str
    text: str


def render_rich_text_document(value: dict[str, Any]) -> RichTextRenderResult:
    if value.get("type") != "doc":
        raise ValueError("富文本根节点必须是 doc")

    blocks = value.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("富文本正文不能为空")

    html_parts: list[str] = []
    text_parts: list[str] = []

    for block in blocks:
        block_html, block_text = _render_block(block)
        if block_text.strip():
            html_parts.append(block_html)
            text_parts.append(block_text)

    html = "".join(html_parts).strip()
    text = "\n".join(text_parts).strip()
    if not text:
        raise ValueError("富文本正文缺少可见文本")
    return RichTextRenderResult(html=html, text=text)


def normalize_email_html(value: str) -> RichTextRenderResult:
    html = sanitize_email_html(value)
    text = html_to_text(html)
    if not text:
        raise ValueError("HTML 正文缺少可见文本")
    return RichTextRenderResult(html=html, text=text)


def text_to_email_html(value: str) -> RichTextRenderResult:
    text = value.strip()
    if not text:
        raise ValueError("纯文本正文不能为空")
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    html = "".join(f"<p>{escape(paragraph)}</p>" for paragraph in paragraphs)
    return RichTextRenderResult(html=html, text="\n".join(paragraphs))


def sanitize_email_html(value: str) -> str:
    soup = BeautifulSoup(value.strip(), "html.parser")
    for tag in soup.find_all(True):
        if tag.name not in {"a", "br", "em", "i", "li", "ol", "p", "span", "strong", "b", "u", "ul"}:
            tag.unwrap()
            continue
        attrs = dict(tag.attrs)
        tag.attrs.clear()
        if tag.name == "a":
            href = str(attrs.get("href", "")).strip()
            _validate_href(href)
            tag.attrs["href"] = href
            tag.attrs["target"] = "_blank"
    normalized = str(soup).strip()
    if not normalized:
        raise ValueError("HTML 正文不能为空")
    return normalized


def html_to_text(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    lines: list[str] = []
    for element in soup.find_all(["p", "li"]):
        text = element.get_text(" ", strip=True)
        if not text:
            continue
        if element.name == "li":
            lines.append(f"- {text}")
        else:
            lines.append(text)
    if lines:
        return "\n".join(lines).strip()
    return soup.get_text(" ", strip=True)


def _render_block(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise ValueError("富文本块必须是对象")
    node_type = value.get("type")
    if node_type == "paragraph":
        html, text = _render_inline_children(value.get("children", []))
        return f"<p>{html}</p>", text
    if node_type in {"bullet_list", "numbered_list"}:
        items = value.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("列表不能为空")
        tag = "ul" if node_type == "bullet_list" else "ol"
        html_items: list[str] = []
        text_items: list[str] = []
        for index, item in enumerate(items, start=1):
            item_html, item_text = _render_inline_children(item)
            html_items.append(f"<li>{item_html}</li>")
            prefix = "-" if node_type == "bullet_list" else f"{index}."
            text_items.append(f"{prefix} {item_text}")
        return f"<{tag}>{''.join(html_items)}</{tag}>", "\n".join(text_items)
    raise ValueError(f"不支持的富文本块类型: {node_type}")


def _render_inline_children(children: Any) -> tuple[str, str]:
    if not isinstance(children, list):
        raise ValueError("富文本子节点必须是数组")
    html_parts: list[str] = []
    text_parts: list[str] = []
    for child in children:
        html, text = _render_inline(child)
        html_parts.append(html)
        text_parts.append(text)
    return "".join(html_parts), "".join(text_parts)


def _render_inline(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise ValueError("富文本内联节点必须是对象")
    node_type = value.get("type")
    if node_type == "text":
        text = str(value.get("text", ""))
        return escape(text), text
    if node_type in {"strong", "emphasis", "link"}:
        html, text = _render_inline_children(value.get("children", []))
        if node_type == "strong":
            return f"<strong>{html}</strong>", text
        if node_type == "emphasis":
            return f"<em>{html}</em>", text
        href = str(value.get("href", "")).strip()
        _validate_href(href)
        return f'<a href="{escape(href, quote=True)}" target="_blank">{html}</a>', text
    if node_type == "line_break":
        return "<br>", "\n"
    raise ValueError(f"不支持的富文本内联类型: {node_type}")


def _validate_href(href: str) -> None:
    parsed = urlparse(href)
    if parsed.scheme not in ALLOWED_LINK_SCHEMES:
        raise ValueError("不支持的链接协议")
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend && uv run python -m unittest backend.test.test_rich_text`

预期：PASS，输出包含 `OK`。

- [ ] **步骤 5：Commit**

运行：

```bash
git add backend/app/services/rich_text.py backend/test/test_rich_text.py
git commit -m "feat(backend): add rich text renderer"
```

## 任务 2：模板导入与保存改为 HTML 主导

**文件：**
- 修改：`backend/app/services/outreach_templates.py`
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/app/services/test_compose_runtime.py`
- 测试：`backend/test/test_database_schema.py`

- [ ] **步骤 1：编写失败的模板导入断言**

在 `backend/test/test_database_schema.py` 增加测试函数：

```python
from app.services.outreach_templates import import_outreach_template_file


def test_html_template_import_derives_text_from_sanitized_html() -> None:
    imported = import_outreach_template_file(
        "template.html",
        b'<p>Hello <strong>{{name}}</strong></p><script>alert(1)</script>',
    )

    assert imported.body_html == "<p>Hello <strong>{{name}}</strong></p>"
    assert imported.body_text == "Hello {{name}}"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest backend.test.test_database_schema`

预期：FAIL，断言显示当前 HTML 清洗没有使用 `rich_text.sanitize_email_html`。

- [ ] **步骤 3：使用富文本内核规范化导入结果**

在 `backend/app/services/outreach_templates.py` 中引入：

```python
from app.services.rich_text import normalize_email_html, text_to_email_html
```

修改 `.html` 导入分支：

```python
normalized = normalize_email_html(html_content)
return ImportedOutreachTemplate(
    subject=None,
    body_text=normalized.text,
    body_html=normalized.html,
    format_name=suffix.lstrip("."),
)
```

修改 `.txt` / `.md` 返回分支：

```python
rendered = text_to_email_html(body_text)
return ImportedOutreachTemplate(
    subject=None,
    body_text=rendered.text,
    body_html=rendered.html,
    format_name=suffix.lstrip("."),
)
```

- [ ] **步骤 4：保存和发送时规范化 HTML**

在 `backend/app/services/task_runtime.py` 中引入：

```python
from app.services.rich_text import normalize_email_html, text_to_email_html
```

将 `apply_task_approval_payload` 中的正文赋值替换为：

```python
task.approved_subject = (payload.subject or task.generated_subject or "").strip()
if payload.body_html:
    rendered = normalize_email_html(payload.body_html)
else:
    rendered = text_to_email_html(payload.body_text)
task.approved_body_text = rendered.text
task.approved_body_html = rendered.html
```

在 `backend/app/services/test_compose_runtime.py` 中引入同样函数，并在保存草稿和发送测试邮件时使用：

```python
if payload.body_html:
    rendered = normalize_email_html(payload.body_html)
else:
    rendered = text_to_email_html(payload.body_text)
body_text = rendered.text
body_html = rendered.html
```

- [ ] **步骤 5：运行后端相关测试**

运行：`cd backend && uv run python -m unittest backend.test.test_database_schema backend.test.test_api_endpoints`

预期：PASS，输出包含 `OK`。

- [ ] **步骤 6：Commit**

运行：

```bash
git add backend/app/services/outreach_templates.py backend/app/services/task_runtime.py backend/app/services/test_compose_runtime.py backend/test/test_database_schema.py
git commit -m "refactor(backend): normalize rich email html"
```

## 任务 3：LLM 改为受控富文本 JSON 输出

**文件：**
- 修改：`backend/app/services/llm_runtime.py`
- 创建：`backend/test/test_llm_rich_draft.py`

- [ ] **步骤 1：编写失败的 LLM 结构化解析测试**

创建 `backend/test/test_llm_rich_draft.py`：

```python
import unittest

from app.services.llm_runtime import DraftGenerationResult, parse_structured_result


class LLMRichDraftTest(unittest.TestCase):
    def test_draft_generation_parses_rich_body_json(self) -> None:
        result = parse_structured_result(
            """
            {
              "subject": "申请交流科研方向",
              "rich_body": {
                "type": "doc",
                "blocks": [
                  {
                    "type": "paragraph",
                    "children": [
                      {"type": "text", "text": "王老师您好，"},
                      {"type": "strong", "children": [{"type": "text", "text": "我很关注您的工作"}]}
                    ]
                  }
                ]
              },
              "suggested_material_ids": [1]
            }
            """,
            DraftGenerationResult,
        )

        self.assertEqual(result.body_text, "王老师您好，我很关注您的工作")
        self.assertEqual(result.body_html, "<p>王老师您好，<strong>我很关注您的工作</strong></p>")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest backend.test.test_llm_rich_draft`

预期：FAIL，报错包含 `Field required` 或 `rich_body` 未被模型识别。

- [ ] **步骤 3：调整 LLM 结果模型**

在 `backend/app/services/llm_runtime.py` 中为 `DraftGenerationResult` 增加字段，并保持旧字段兼容：

```python
class DraftGenerationResult(BaseModel):
    subject: str
    body_text: str | None = None
    body_html: str | None = None
    rich_body: dict[str, object] | None = None
    suggested_material_ids: list[int] = Field(default_factory=list)
```

将 `MatchAndDraftResult` 的正文部分改成同样字段：

```python
body_text: str | None = None
body_html: str | None = None
rich_body: dict[str, object] | None = None
```

- [ ] **步骤 4：用富文本渲染替换草稿规范化逻辑**

在 `backend/app/services/llm_runtime.py` 中引入：

```python
from app.services.rich_text import normalize_email_html, render_rich_text_document, text_to_email_html
```

将 `_normalize_draft_generation_result` 的正文处理改为：

```python
if result.rich_body is not None:
    rendered = render_rich_text_document(result.rich_body)
elif result.body_html:
    rendered = normalize_email_html(result.body_html)
elif result.body_text:
    rendered = text_to_email_html(result.body_text)
else:
    raise LLMRuntimeError("模型返回的富文本正文为空")

result.body_text = rendered.text
result.body_html = rendered.html
```

- [ ] **步骤 5：更新 LLM prompt 要求 JSON 富文本**

在 `SYSTEM_DRAFT_PROMPT` 和 `SYSTEM_MATCH_AND_DRAFT_PROMPT` 中把 `body_html` 要求替换为：

```text
你必须输出 JSON 对象。邮件正文不要直接输出 HTML。
正文使用 rich_body 字段，结构为：
{
  "type": "doc",
  "blocks": [
    {
      "type": "paragraph",
      "children": [
        {"type": "text", "text": "正文"},
        {"type": "strong", "children": [{"type": "text", "text": "重点"}]}
      ]
    }
  ]
}
允许的块类型只有 paragraph、bullet_list、numbered_list。
允许的内联类型只有 text、strong、emphasis、link、line_break。
link 的 href 只能使用 http、https、mailto。
```

在 `build_draft_prompt` 和 `build_match_and_draft_prompt` 的任务要求中删除“body_html 必须是可直接发送的 HTML”，改成“rich_body 必须可渲染为邮件正文”。

- [ ] **步骤 6：运行 LLM 和后端测试**

运行：`cd backend && uv run python -m unittest backend.test.test_llm_rich_draft backend.test.test_api_endpoints`

预期：PASS，输出包含 `OK`。

- [ ] **步骤 7：Commit**

运行：

```bash
git add backend/app/services/llm_runtime.py backend/test/test_llm_rich_draft.py
git commit -m "feat(backend): parse llm rich draft json"
```

## 任务 4：前端富文本工具和编辑器

**文件：**
- 创建：`frontend/src/lib/richEmail.ts`
- 创建：`frontend/src/components/molecules/RichEmailEditor.tsx`
- 创建：`frontend/test/richEmail.test.ts`
- 创建：`frontend/test/RichEmailEditor.test.tsx`

- [ ] **步骤 1：编写失败的富文本工具测试**

创建 `frontend/test/richEmail.test.ts`：

```typescript
import { describe, expect, it } from "vitest";
import { deriveTextFromEmailHtml, normalizeEmailHtml } from "@/lib/richEmail";

describe("richEmail", () => {
  it("normalizes html and derives plain text", () => {
    const html = normalizeEmailHtml("<p>王老师您好</p><script>alert(1)</script><ul><li>研究方向匹配</li></ul>");

    expect(html).toBe("<p>王老师您好</p><ul><li>研究方向匹配</li></ul>");
    expect(deriveTextFromEmailHtml(html)).toBe("王老师您好\n- 研究方向匹配");
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm test -- richEmail.test.ts`

预期：FAIL，报错包含 `Failed to resolve import "@/lib/richEmail"`。

- [ ] **步骤 3：实现前端富文本工具**

创建 `frontend/src/lib/richEmail.ts`：

```typescript
import DOMPurify from "dompurify";

const ALLOWED_TAGS = ["a", "br", "em", "i", "li", "ol", "p", "span", "strong", "b", "u", "ul"];
const ALLOWED_ATTR = ["href", "target"];

export const normalizeEmailHtml = (value: string): string =>
  DOMPurify.sanitize(value.trim(), {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
    FORBID_TAGS: ["script", "style"],
  }).trim();

export const deriveTextFromEmailHtml = (value: string): string => {
  const container = document.createElement("div");
  container.innerHTML = normalizeEmailHtml(value);
  const lines: string[] = [];
  container.querySelectorAll("p, li").forEach((element) => {
    const text = element.textContent?.replace(/\s+/g, " ").trim();
    if (!text) {
      return;
    }
    lines.push(element.tagName.toLowerCase() === "li" ? `- ${text}` : text);
  });
  return lines.length > 0 ? lines.join("\n") : container.textContent?.replace(/\s+/g, " ").trim() ?? "";
};

export const textToEmailHtml = (value: string): string =>
  value
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => `<p>${escapeHtml(line)}</p>`)
    .join("");

const escapeHtml = (value: string): string =>
  value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
```

- [ ] **步骤 4：运行富文本工具测试通过**

运行：`cd frontend && npm test -- richEmail.test.ts`

预期：PASS。

- [ ] **步骤 5：编写失败的编辑器测试**

创建 `frontend/test/RichEmailEditor.test.tsx`：

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RichEmailEditor } from "@/components/molecules/RichEmailEditor";

describe("RichEmailEditor", () => {
  it("emits html and text after editing content", () => {
    const handleChange = vi.fn();

    render(
      <RichEmailEditor
        label="邮件正文"
        html="<p>王老师您好</p>"
        onChange={handleChange}
      />,
    );

    const editor = screen.getByRole("textbox", { name: "邮件正文" });
    fireEvent.input(editor, {
      currentTarget: { innerHTML: "<p>王老师您好</p><p><strong>我很关注您的研究</strong></p>" },
    });

    expect(handleChange).toHaveBeenLastCalledWith({
      html: "<p>王老师您好</p><p><strong>我很关注您的研究</strong></p>",
      text: "王老师您好\n我很关注您的研究",
    });
  });
});
```

- [ ] **步骤 6：运行编辑器测试验证失败**

运行：`cd frontend && npm test -- RichEmailEditor.test.tsx`

预期：FAIL，报错包含 `Failed to resolve import "@/components/molecules/RichEmailEditor"`。

- [ ] **步骤 7：实现最小富文本编辑器**

创建 `frontend/src/components/molecules/RichEmailEditor.tsx`：

```tsx
import { useEffect, useRef } from "react";
import { Bold, Italic, Link, List, ListOrdered } from "lucide-react";
import { deriveTextFromEmailHtml, normalizeEmailHtml } from "@/lib/richEmail";

type RichEmailValue = {
  html: string;
  text: string;
};

type RichEmailEditorProps = {
  label: string;
  html: string;
  onChange: (value: RichEmailValue) => void;
};

export const RichEmailEditor = ({ label, html, onChange }: RichEmailEditorProps) => {
  const editorRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (editorRef.current && editorRef.current.innerHTML !== html) {
      editorRef.current.innerHTML = html;
    }
  }, [html]);

  const emitChange = () => {
    const nextHtml = normalizeEmailHtml(editorRef.current?.innerHTML ?? "");
    onChange({
      html: nextHtml,
      text: deriveTextFromEmailHtml(nextHtml),
    });
  };

  const applyCommand = (command: string, value?: string) => {
    document.execCommand(command, false, value);
    emitChange();
  };

  return (
    <div className="block">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
        <label id="rich-email-editor-label" className="text-sm font-medium text-stone-800">
          {label}
        </label>
        <div className="flex flex-wrap gap-1 rounded-2xl border border-stone-200 bg-stone-50 p-1">
          <button type="button" aria-label="加粗" onClick={() => applyCommand("bold")} className="rounded-xl p-2 text-stone-600 hover:bg-white">
            <Bold className="h-4 w-4" />
          </button>
          <button type="button" aria-label="斜体" onClick={() => applyCommand("italic")} className="rounded-xl p-2 text-stone-600 hover:bg-white">
            <Italic className="h-4 w-4" />
          </button>
          <button type="button" aria-label="无序列表" onClick={() => applyCommand("insertUnorderedList")} className="rounded-xl p-2 text-stone-600 hover:bg-white">
            <List className="h-4 w-4" />
          </button>
          <button type="button" aria-label="有序列表" onClick={() => applyCommand("insertOrderedList")} className="rounded-xl p-2 text-stone-600 hover:bg-white">
            <ListOrdered className="h-4 w-4" />
          </button>
          <button
            type="button"
            aria-label="插入链接"
            onClick={() => {
              const href = window.prompt("请输入链接地址");
              if (href) {
                applyCommand("createLink", href);
              }
            }}
            className="rounded-xl p-2 text-stone-600 hover:bg-white"
          >
            <Link className="h-4 w-4" />
          </button>
        </div>
      </div>
      <div
        ref={editorRef}
        role="textbox"
        aria-labelledby="rich-email-editor-label"
        contentEditable
        suppressContentEditableWarning
        onInput={emitChange}
        className="min-h-[320px] rounded-[28px] border border-stone-200 bg-white px-4 py-4 text-sm leading-7 text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15"
      />
    </div>
  );
};
```

- [ ] **步骤 8：运行前端编辑器测试通过**

运行：`cd frontend && npm test -- richEmail.test.ts RichEmailEditor.test.tsx`

预期：PASS。

- [ ] **步骤 9：Commit**

运行：

```bash
git add frontend/src/lib/richEmail.ts frontend/src/components/molecules/RichEmailEditor.tsx frontend/test/richEmail.test.ts frontend/test/RichEmailEditor.test.tsx
git commit -m "feat(frontend): add rich email editor"
```

## 任务 5：个人页默认模板改为富文本

**文件：**
- 修改：`frontend/src/pages/ProfilePage.tsx`
- 修改：`frontend/test/ProfilePageOnboarding.test.tsx`

- [ ] **步骤 1：编写失败的个人页断言**

在 `frontend/test/ProfilePageOnboarding.test.tsx` 增加断言，替换旧的纯文本/HTML 双输入预期：

```tsx
expect(screen.getByText("默认模板正文")).toBeInTheDocument();
expect(screen.queryByText("默认模板正文（纯文本）")).not.toBeInTheDocument();
expect(screen.queryByText("默认模板正文（HTML，可保留格式）")).not.toBeInTheDocument();
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm test -- ProfilePageOnboarding.test.tsx`

预期：FAIL，页面仍显示 `默认模板正文（纯文本）` 或 `默认模板正文（HTML，可保留格式）`。

- [ ] **步骤 3：替换个人页模板编辑 UI**

在 `frontend/src/pages/ProfilePage.tsx` 中删除 `HtmlTemplateEditorField` 引入，新增：

```tsx
import { RichEmailEditor } from "@/components/molecules/RichEmailEditor";
import { deriveTextFromEmailHtml, textToEmailHtml } from "@/lib/richEmail";
```

将模板弹窗中的纯文本 textarea 与 `HtmlTemplateEditorField` 替换为：

```tsx
<RichEmailEditor
  label="默认模板正文"
  html={form.outreach_template_body_html || textToEmailHtml(form.outreach_template_body_text)}
  onChange={({ html, text }) => {
    onBodyHtmlChange(html);
    onBodyTextChange(text);
  }}
/>
```

将摘要文案从：

```tsx
纯文本正文（必填）：...
HTML 正文（可选）：...
```

改为：

```tsx
富文本正文（必填）：{form.outreach_template_body_html.trim() || form.outreach_template_body_text.trim() ? '已填写' : '未填写'}
```

- [ ] **步骤 4：更新导入成功提示**

将 `handleTemplateFileImport` 中的成功提示改为：

```tsx
hasSubject
  ? `已导入 ${imported.format_name} 模板文件，并转换为可编辑富文本。`
  : `已导入 ${imported.format_name} 模板文件，并转换为可编辑富文本。请继续填写模板主题后再保存身份。`
```

- [ ] **步骤 5：运行个人页测试通过**

运行：`cd frontend && npm test -- ProfilePageOnboarding.test.tsx`

预期：PASS。

- [ ] **步骤 6：Commit**

运行：

```bash
git add frontend/src/pages/ProfilePage.tsx frontend/test/ProfilePageOnboarding.test.tsx
git commit -m "feat(frontend): use rich editor for identity templates"
```

## 任务 6：测试写信页接入富文本编辑器

**文件：**
- 修改：`frontend/src/pages/TestComposePage.tsx`
- 修改：`frontend/test/TestComposePage.test.tsx`

- [ ] **步骤 1：编写失败的测试写信页断言**

在 `frontend/test/TestComposePage.test.tsx` 中把正文断言改为：

```tsx
expect(await screen.findByRole("textbox", { name: "邮件正文" })).toHaveTextContent("测试正文");
expect(screen.queryByDisplayValue("测试正文")).not.toBeInTheDocument();
```

新增保存 payload 测试：

```tsx
fireEvent.input(screen.getByRole("textbox", { name: "邮件正文" }), {
  currentTarget: { innerHTML: "<p>更新后的正文</p>" },
});
fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
expect(mockedSaveTestComposeDraft).toHaveBeenCalledWith(1, 1, {
  subject: "测试主题",
  body_text: "更新后的正文",
  body_html: "<p>更新后的正文</p>",
  selected_material_ids: [],
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm test -- TestComposePage.test.tsx`

预期：FAIL，页面仍使用 textarea。

- [ ] **步骤 3：改造测试写信页状态**

在 `frontend/src/pages/TestComposePage.tsx` 中新增：

```tsx
const [bodyHtml, setBodyHtml] = useState("");
```

将 `syncDraft` 改为：

```tsx
setBodyText(nextThread.draft.body_text);
setBodyHtml(nextThread.draft.body_html || textToEmailHtml(nextThread.draft.body_text));
```

- [ ] **步骤 4：替换正文 textarea**

在 `frontend/src/pages/TestComposePage.tsx` 中引入 `RichEmailEditor`，将正文 textarea 替换为：

```tsx
<RichEmailEditor
  label="邮件正文"
  html={bodyHtml}
  onChange={({ html, text }) => {
    setBodyHtml(html);
    setBodyText(text);
  }}
/>
```

保存和发送 payload 使用：

```tsx
body_text: bodyText,
body_html: bodyHtml,
```

- [ ] **步骤 5：运行测试写信页测试通过**

运行：`cd frontend && npm test -- TestComposePage.test.tsx`

预期：PASS。

- [ ] **步骤 6：Commit**

运行：

```bash
git add frontend/src/pages/TestComposePage.tsx frontend/test/TestComposePage.test.tsx
git commit -m "feat(frontend): use rich editor for test compose"
```

## 任务 7：正式工作区写信区接入富文本编辑器

**文件：**
- 修改：`frontend/src/components/organisms/WorkspaceComposerDock.tsx`
- 修改：`frontend/src/pages/WorkspacePage.tsx`
- 修改：`frontend/test/WorkspaceComposerDockCopy.test.tsx`
- 修改：`frontend/test/WorkspacePageNextStep.test.tsx`

- [ ] **步骤 1：编写失败的工作区组件测试**

在 `frontend/test/WorkspaceComposerDockCopy.test.tsx` 中新增：

```tsx
it("renders a rich email editor for the draft body", () => {
  renderComposer({ draftReady: true, content: "老师您好", contentHtml: "<p>老师您好</p>" });

  expect(screen.getByRole("textbox", { name: "邮件正文" })).toHaveTextContent("老师您好");
  expect(screen.queryByText("系统会自动切回普通文本")).not.toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm test -- WorkspaceComposerDockCopy.test.tsx`

预期：FAIL，当前组件仍显示 textarea 和旧提示。

- [ ] **步骤 3：调整 `WorkspaceComposerDock` props**

将 `frontend/src/components/organisms/WorkspaceComposerDock.tsx` 中 props 改为：

```tsx
content: string;
contentHtml: string;
onContentChange: (value: { html: string; text: string }) => void;
```

删除 `hasRichHtml` prop。

- [ ] **步骤 4：替换正式正文 textarea**

在 `WorkspaceComposerDock` 中引入并使用：

```tsx
<RichEmailEditor
  label="邮件正文"
  html={contentHtml}
  onChange={onContentChange}
/>
```

删除旧的 `hasRichHtml` 提示块。

- [ ] **步骤 5：调整 `WorkspacePage` 状态流**

在 `frontend/src/pages/WorkspacePage.tsx` 中引入：

```tsx
import { textToEmailHtml } from "@/lib/richEmail";
```

将 `handleContentChange` 改为：

```tsx
const handleContentChange = useCallback((value: { html: string; text: string }) => {
  setContent(value.text);
  setContentHtml(value.html);
}, []);
```

传给组件：

```tsx
content={content}
contentHtml={contentHtml || textToEmailHtml(content)}
onContentChange={handleContentChange}
```

- [ ] **步骤 6：更新工作区测试 mock**

在 `frontend/test/WorkspacePageNextStep.test.tsx` 的 `WorkspaceComposerDock` mock props 类型中加入：

```tsx
contentHtml: string;
onContentChange: (value: { html: string; text: string }) => void;
```

保留现有 HTML-only payload 断言，预期仍是：

```tsx
body_text: "老师您好\n我想请教一个研究问题。",
body_html: "<p>老师您好</p><p>我想请教一个研究问题。</p>",
```

- [ ] **步骤 7：运行工作区前端测试**

运行：`cd frontend && npm test -- WorkspaceComposerDockCopy.test.tsx WorkspacePageNextStep.test.tsx`

预期：PASS。

- [ ] **步骤 8：Commit**

运行：

```bash
git add frontend/src/components/organisms/WorkspaceComposerDock.tsx frontend/src/pages/WorkspacePage.tsx frontend/test/WorkspaceComposerDockCopy.test.tsx frontend/test/WorkspacePageNextStep.test.tsx
git commit -m "feat(frontend): use rich editor in workspace composer"
```

## 任务 8：全量验证

**文件：**
- 修改：无

- [ ] **步骤 1：运行后端测试**

运行：`cd backend && uv run python -m unittest discover test`

预期：PASS，输出包含 `OK`。

- [ ] **步骤 2：运行前端关键测试**

运行：

```bash
cd frontend
npm test -- richEmail.test.ts RichEmailEditor.test.tsx ProfilePageOnboarding.test.tsx TestComposePage.test.tsx WorkspaceComposerDockCopy.test.tsx WorkspacePageNextStep.test.tsx
```

预期：PASS，所有列出的测试文件通过。

- [ ] **步骤 3：运行前端 lint**

运行：`cd frontend && npm run lint`

预期：退出码 0，输出包含 `eslint .` 且没有 error。

- [ ] **步骤 4：运行前端构建**

运行：`cd frontend && npm run build`

预期：退出码 0，输出包含 `✓ built`。

- [ ] **步骤 5：检查工作区差异**

运行：`git status --short`

预期：只显示本计划执行过程中准备提交的文件；没有临时文件、构建产物、`.env`、`node_modules`。

- [ ] **步骤 6：Commit 验证记录**

如果步骤 1 到步骤 5 都通过，运行：

```bash
git add docs/superpowers/plans/2026-04-23-rich-email-editor-and-llm-json.md
git commit -m "docs: add rich email implementation plan"
```
