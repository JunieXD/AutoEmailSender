# Tiptap 邮件编辑器实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 用 `Tiptap` 一次性替换个人页模板弹窗、测试写信页和正式工作区写信区的现有邮件正文编辑器，并提供第一版字体、段落、链接、表格和 HTML 预览能力。

**架构：** 前端新增一个共用 `EmailTemplateEditor`，基于 `Tiptap` 管理 HTML 内容并派生纯文本；三处页面全部改用这个组件。后端现有 `body_html` / `body_text` 保存和发送契约保持不变，仅依赖已经存在的 HTML 清洗与纯文本派生逻辑做最终兜底。

**技术栈：** React 19、Vite、Vitest、Tiptap React、Tiptap StarterKit、Tiptap Table 扩展、DOMPurify、现有 FastAPI/SQLAlchemy 后端契约。

---

## 文件结构

- 修改：`frontend/package.json`
  - 增加 `tiptap` 相关依赖。
- 创建：`frontend/src/components/molecules/EmailTemplateEditor.tsx`
  - 基于 Tiptap 的共用邮件编辑器，支持字体、字号、段落、链接、表格、HTML 预览。
- 创建：`frontend/src/components/molecules/tiptap/FontFamily.ts`
  - 自定义字体扩展。
- 创建：`frontend/src/components/molecules/tiptap/FontSize.ts`
  - 自定义字号扩展。
- 创建：`frontend/src/components/molecules/tiptap/LineHeight.ts`
  - 自定义行距扩展。
- 创建：`frontend/src/components/molecules/tiptap/FirstLineIndent.ts`
  - 自定义首行缩进扩展。
- 创建：`frontend/src/components/molecules/tiptap/emailEditorStyles.ts`
  - 统一编辑器内容样式、字体候选、字号候选、行距候选。
- 修改：`frontend/src/lib/richEmail.ts`
  - 保留 HTML 规范化与纯文本派生，但适配 Tiptap 输出 HTML。
- 修改：`frontend/src/pages/ProfilePage.tsx`
  - 模板弹窗替换为 `EmailTemplateEditor`。
- 修改：`frontend/src/pages/TestComposePage.tsx`
  - 测试写信页正文替换为 `EmailTemplateEditor`。
- 修改：`frontend/src/components/organisms/WorkspaceComposerDock.tsx`
  - 正式工作区正文替换为 `EmailTemplateEditor`。
- 修改：`frontend/src/pages/WorkspacePage.tsx`
  - 调整工作区正文状态为 `html + text` 的同步流。
- 创建：`frontend/test/EmailTemplateEditor.test.tsx`
  - 覆盖 Tiptap 编辑器基础渲染、字体/段落/表格操作和 HTML 预览。
- 修改：`frontend/test/richEmail.test.ts`
  - 确认 Tiptap 输出 HTML 在清洗后仍保留邮件场景关键样式。
- 修改：`frontend/test/ProfilePageOnboarding.test.tsx`
  - 更新个人页模板弹窗的正文编辑断言。
- 修改：`frontend/test/TestComposePage.test.tsx`
  - 更新测试写信页的正文编辑和保存断言。
- 修改：`frontend/test/WorkspaceComposerDockCopy.test.tsx`
  - 更新正式工作区编辑器断言。
- 修改：`frontend/test/WorkspacePageNextStep.test.tsx`
  - 更新工作区发送 payload 断言。

## 任务 1：引入 Tiptap 依赖与编辑器测试骨架

**文件：**
- 修改：`frontend/package.json`
- 创建：`frontend/test/EmailTemplateEditor.test.tsx`

- [ ] **步骤 1：编写失败的编辑器渲染测试**

创建 `frontend/test/EmailTemplateEditor.test.tsx`：

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EmailTemplateEditor } from "@/components/molecules/EmailTemplateEditor";

describe("EmailTemplateEditor", () => {
  it("renders the editor and toolbar controls", () => {
    render(
      <EmailTemplateEditor
        label="默认模板正文"
        html="<p>老师您好</p>"
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("textbox", { name: "默认模板正文" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "加粗" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "插入表格" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "HTML 预览" })).toBeInTheDocument();
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm test -- EmailTemplateEditor.test.tsx`

预期：FAIL，报错 `Failed to resolve import "@/components/molecules/EmailTemplateEditor"`。

- [ ] **步骤 3：在 `package.json` 中添加 Tiptap 依赖**

修改 `frontend/package.json` 的 `dependencies`：

```json
"@tiptap/core": "^2.11.5",
"@tiptap/extension-link": "^2.11.5",
"@tiptap/extension-table": "^2.11.5",
"@tiptap/extension-table-cell": "^2.11.5",
"@tiptap/extension-table-header": "^2.11.5",
"@tiptap/extension-table-row": "^2.11.5",
"@tiptap/extension-text-align": "^2.11.5",
"@tiptap/extension-text-style": "^2.11.5",
"@tiptap/extension-underline": "^2.11.5",
"@tiptap/pm": "^2.11.5",
"@tiptap/react": "^2.11.5",
"@tiptap/starter-kit": "^2.11.5"
```

- [ ] **步骤 4：安装依赖**

运行：`cd frontend && npm install`

预期：安装成功，`package-lock.json` 更新。

- [ ] **步骤 5：Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/test/EmailTemplateEditor.test.tsx
git commit -m "build(frontend): add tiptap dependencies"
```

## 任务 2：实现共用 `EmailTemplateEditor`

**文件：**
- 创建：`frontend/src/components/molecules/EmailTemplateEditor.tsx`
- 创建：`frontend/src/components/molecules/tiptap/FontFamily.ts`
- 创建：`frontend/src/components/molecules/tiptap/FontSize.ts`
- 创建：`frontend/src/components/molecules/tiptap/LineHeight.ts`
- 创建：`frontend/src/components/molecules/tiptap/FirstLineIndent.ts`
- 创建：`frontend/src/components/molecules/tiptap/emailEditorStyles.ts`
- 测试：`frontend/test/EmailTemplateEditor.test.tsx`

- [ ] **步骤 1：编写失败的字体与表格测试**

在 `frontend/test/EmailTemplateEditor.test.tsx` 追加：

```tsx
import { fireEvent } from "@testing-library/react";

it("keeps table html and emits updated html/text", () => {
  const handleChange = vi.fn();

  render(
    <EmailTemplateEditor
      label="邮件正文"
      html='<table style="font-family:SimSun"><tbody><tr><td>老师您好</td></tr></tbody></table>'
      onChange={handleChange}
    />,
  );

  const editor = screen.getByRole("textbox", { name: "邮件正文" });
  editor.innerHTML = '<table style="font-family:SimSun"><tbody><tr><td>老师您好A</td></tr></tbody></table>';
  fireEvent.input(editor);

  expect(handleChange).toHaveBeenLastCalledWith({
    html: expect.stringContaining("<table"),
    text: expect.stringContaining("老师您好A"),
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm test -- EmailTemplateEditor.test.tsx`

预期：FAIL，`EmailTemplateEditor` 尚不存在。

- [ ] **步骤 3：实现自定义扩展**

创建 `frontend/src/components/molecules/tiptap/FontFamily.ts`：

```ts
import { Extension } from "@tiptap/core";
import "@tiptap/extension-text-style";

export const FontFamily = Extension.create({
  name: "fontFamily",
  addGlobalAttributes() {
    return [
      {
        types: ["textStyle"],
        attributes: {
          fontFamily: {
            default: null,
            parseHTML: (element) => element.style.fontFamily || null,
            renderHTML: (attributes) =>
              attributes.fontFamily ? { style: `font-family:${attributes.fontFamily}` } : {},
          },
        },
      },
    ];
  },
});
```

创建 `frontend/src/components/molecules/tiptap/FontSize.ts`：

```ts
import { Extension } from "@tiptap/core";
import "@tiptap/extension-text-style";

export const FontSize = Extension.create({
  name: "fontSize",
  addGlobalAttributes() {
    return [
      {
        types: ["textStyle"],
        attributes: {
          fontSize: {
            default: null,
            parseHTML: (element) => element.style.fontSize || null,
            renderHTML: (attributes) =>
              attributes.fontSize ? { style: `font-size:${attributes.fontSize}` } : {},
          },
        },
      },
    ];
  },
});
```

创建 `frontend/src/components/molecules/tiptap/LineHeight.ts`：

```ts
import { Extension } from "@tiptap/core";

export const LineHeight = Extension.create({
  name: "lineHeight",
  addGlobalAttributes() {
    return [
      {
        types: ["paragraph", "heading", "tableCell", "tableHeader"],
        attributes: {
          lineHeight: {
            default: null,
            parseHTML: (element) => element.style.lineHeight || null,
            renderHTML: (attributes) =>
              attributes.lineHeight ? { style: `line-height:${attributes.lineHeight}` } : {},
          },
        },
      },
    ];
  },
});
```

创建 `frontend/src/components/molecules/tiptap/FirstLineIndent.ts`：

```ts
import { Extension } from "@tiptap/core";

export const FirstLineIndent = Extension.create({
  name: "firstLineIndent",
  addGlobalAttributes() {
    return [
      {
        types: ["paragraph"],
        attributes: {
          firstLineIndent: {
            default: null,
            parseHTML: (element) => element.style.textIndent || null,
            renderHTML: (attributes) =>
              attributes.firstLineIndent ? { style: `text-indent:${attributes.firstLineIndent}` } : {},
          },
        },
      },
    ];
  },
});
```

- [ ] **步骤 4：实现编辑器样式常量**

创建 `frontend/src/components/molecules/tiptap/emailEditorStyles.ts`：

```ts
export const EMAIL_FONT_OPTIONS = [
  { label: "宋体", value: "SimSun, Songti SC, serif" },
  { label: "微软雅黑", value: "Microsoft YaHei, sans-serif" },
  { label: "Times New Roman", value: "Times New Roman, serif" },
];

export const EMAIL_FONT_SIZE_OPTIONS = ["12pt", "14pt", "16pt", "18pt"];
export const EMAIL_LINE_HEIGHT_OPTIONS = ["1.5", "1.75", "2"];
export const EMAIL_FIRST_LINE_INDENT_OPTIONS = ["0", "2em"];
```

- [ ] **步骤 5：实现 `EmailTemplateEditor` 最小版本**

创建 `frontend/src/components/molecules/EmailTemplateEditor.tsx`：

```tsx
import { useEffect, useMemo, useState } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Link from "@tiptap/extension-link";
import Underline from "@tiptap/extension-underline";
import TextAlign from "@tiptap/extension-text-align";
import TextStyle from "@tiptap/extension-text-style";
import Table from "@tiptap/extension-table";
import TableRow from "@tiptap/extension-table-row";
import TableHeader from "@tiptap/extension-table-header";
import TableCell from "@tiptap/extension-table-cell";
import { Bold, Italic, Link2, Table2, Underline as UnderlineIcon } from "lucide-react";
import { deriveTextFromEmailHtml } from "@/lib/richEmail";
import { FontFamily } from "@/components/molecules/tiptap/FontFamily";
import { FontSize } from "@/components/molecules/tiptap/FontSize";
import { LineHeight } from "@/components/molecules/tiptap/LineHeight";
import { FirstLineIndent } from "@/components/molecules/tiptap/FirstLineIndent";

type Props = {
  label: string;
  html: string;
  onChange: (value: { html: string; text: string }) => void;
};

export const EmailTemplateEditor = ({ label, html, onChange }: Props) => {
  const [previewOpen, setPreviewOpen] = useState(false);
  const editor = useEditor({
    extensions: [
      StarterKit,
      Link,
      Underline,
      TextStyle,
      TextAlign.configure({ types: ["heading", "paragraph"] }),
      Table.configure({ resizable: true }),
      TableRow,
      TableHeader,
      TableCell,
      FontFamily,
      FontSize,
      LineHeight,
      FirstLineIndent,
    ],
    content: html,
    immediatelyRender: false,
    onUpdate: ({ editor }) => {
      const nextHtml = editor.getHTML();
      onChange({
        html: nextHtml,
        text: deriveTextFromEmailHtml(nextHtml),
      });
    },
  });

  useEffect(() => {
    if (editor && html !== editor.getHTML()) {
      editor.commands.setContent(html, { emitUpdate: false });
    }
  }, [editor, html]);

  const contentHtml = useMemo(() => editor?.getHTML() ?? html, [editor, html]);

  if (!editor) return null;

  return (
    <div className="block">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="text-sm font-medium text-stone-800">{label}</div>
        <div className="flex flex-wrap gap-1 rounded-2xl border border-stone-200 bg-stone-50 p-1">
          <button type="button" aria-label="加粗" onClick={() => editor.chain().focus().toggleBold().run()} className="rounded-xl p-2 text-stone-600 hover:bg-white"><Bold className="h-4 w-4" /></button>
          <button type="button" aria-label="斜体" onClick={() => editor.chain().focus().toggleItalic().run()} className="rounded-xl p-2 text-stone-600 hover:bg-white"><Italic className="h-4 w-4" /></button>
          <button type="button" aria-label="下划线" onClick={() => editor.chain().focus().toggleUnderline().run()} className="rounded-xl p-2 text-stone-600 hover:bg-white"><UnderlineIcon className="h-4 w-4" /></button>
          <button type="button" aria-label="插入链接" onClick={() => editor.chain().focus().setLink({ href: "https://example.com" }).run()} className="rounded-xl p-2 text-stone-600 hover:bg-white"><Link2 className="h-4 w-4" /></button>
          <button type="button" aria-label="插入表格" onClick={() => editor.chain().focus().insertTable({ rows: 2, cols: 2, withHeaderRow: true }).run()} className="rounded-xl p-2 text-stone-600 hover:bg-white"><Table2 className="h-4 w-4" /></button>
          <button type="button" aria-label="HTML 预览" onClick={() => setPreviewOpen((current) => !current)} className="rounded-xl px-3 py-2 text-xs text-stone-600 hover:bg-white">HTML 预览</button>
        </div>
      </div>
      <EditorContent editor={editor} aria-label={label} className="min-h-[320px] rounded-[28px] border border-stone-200 bg-white px-4 py-4 text-sm text-stone-700" />
      {previewOpen ? (
        <div className="mt-4 rounded-2xl border border-stone-200 bg-stone-50 p-4">
          <div dangerouslySetInnerHTML={{ __html: contentHtml }} />
        </div>
      ) : null}
    </div>
  );
};
```

- [ ] **步骤 6：运行测试验证通过**

运行：`cd frontend && npm test -- EmailTemplateEditor.test.tsx`

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
git add frontend/src/components/molecules/EmailTemplateEditor.tsx frontend/src/components/molecules/tiptap frontend/test/EmailTemplateEditor.test.tsx
git commit -m "feat(frontend): add tiptap email editor"
```

## 任务 3：替换个人页模板弹窗

**文件：**
- 修改：`frontend/src/pages/ProfilePage.tsx`
- 修改：`frontend/test/ProfilePageOnboarding.test.tsx`

- [ ] **步骤 1：编写失败的个人页弹窗测试**

在 `frontend/test/ProfilePageOnboarding.test.tsx` 新增：

```tsx
it("uses the tiptap email editor for the default template body", async () => {
  renderPage();
  fireEvent.click(screen.getByRole("button", { name: "打开默认值编辑" }));

  expect(await screen.findByRole("heading", { name: "默认发信模式与默认模板" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "加粗" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "插入表格" })).toBeInTheDocument();
  expect(screen.queryByText("默认模板正文（纯文本）")).not.toBeInTheDocument();
  expect(screen.queryByText("默认模板正文（HTML，可保留格式）")).not.toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm test -- ProfilePageOnboarding.test.tsx`

预期：FAIL，仍然使用旧输入区域。

- [ ] **步骤 3：替换个人页模板正文区域**

在 `frontend/src/pages/ProfilePage.tsx`：

- 删除：

```tsx
import { HtmlTemplateEditorField } from "@/components/molecules/HtmlTemplateEditorField";
```

- 新增：

```tsx
import { EmailTemplateEditor } from "@/components/molecules/EmailTemplateEditor";
```

- 将旧的纯文本 textarea 和 `HtmlTemplateEditorField` 块替换为：

```tsx
<EmailTemplateEditor
  label="默认模板正文"
  html={form.outreach_template_body_html}
  onChange={({ html, text }) => {
    onBodyHtmlChange(html);
    onBodyTextChange(text);
  }}
/>
```

- 将摘要文案中“纯文本正文 / HTML 正文”合并为：

```tsx
<span className="rounded-full border border-stone-200 bg-white/90 px-3 py-1">
  富文本正文（必填）：{form.outreach_template_body_html.trim() ? "已填写" : "未填写"}
</span>
```

- [ ] **步骤 4：更新导入提示文案**

在 `handleTemplateFileImport` 成功提示中，替换为：

```tsx
hasSubject
  ? `已导入 ${imported.format_name} 模板文件，并转换为可编辑富文本。`
  : `已导入 ${imported.format_name} 模板文件，并转换为可编辑富文本。请继续填写模板主题后再保存身份。`
```

- [ ] **步骤 5：运行测试验证通过**

运行：`cd frontend && npm test -- ProfilePageOnboarding.test.tsx`

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add frontend/src/pages/ProfilePage.tsx frontend/test/ProfilePageOnboarding.test.tsx
git commit -m "feat(frontend): use tiptap editor for identity templates"
```

## 任务 4：替换测试写信页

**文件：**
- 修改：`frontend/src/pages/TestComposePage.tsx`
- 修改：`frontend/test/TestComposePage.test.tsx`

- [ ] **步骤 1：编写失败的测试写信页测试**

在 `frontend/test/TestComposePage.test.tsx` 追加：

```tsx
it("uses the tiptap email editor in test compose", async () => {
  render(
    <MemoryRouter>
      <TestComposePage />
    </MemoryRouter>,
  );

  expect(await screen.findByRole("textbox", { name: "邮件正文" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "插入表格" })).toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm test -- TestComposePage.test.tsx`

预期：FAIL，当前页面仍使用旧编辑器。

- [ ] **步骤 3：替换测试写信页编辑器**

在 `frontend/src/pages/TestComposePage.tsx`：

- 删除：

```tsx
import { RichEmailEditor } from "@/components/molecules/RichEmailEditor";
```

- 新增：

```tsx
import { EmailTemplateEditor } from "@/components/molecules/EmailTemplateEditor";
```

- 替换正文区域：

```tsx
<EmailTemplateEditor
  label="邮件正文"
  html={bodyHtml}
  onChange={({ html, text }) => {
    setBodyHtml(html);
    setBodyText(text);
  }}
/>
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd frontend && npm test -- TestComposePage.test.tsx`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/pages/TestComposePage.tsx frontend/test/TestComposePage.test.tsx
git commit -m "feat(frontend): use tiptap editor for test compose"
```

## 任务 5：替换正式工作区写信区

**文件：**
- 修改：`frontend/src/components/organisms/WorkspaceComposerDock.tsx`
- 修改：`frontend/src/pages/WorkspacePage.tsx`
- 修改：`frontend/test/WorkspaceComposerDockCopy.test.tsx`
- 修改：`frontend/test/WorkspacePageNextStep.test.tsx`

- [ ] **步骤 1：编写失败的工作区测试**

在 `frontend/test/WorkspaceComposerDockCopy.test.tsx` 中把富文本断言收紧为：

```tsx
it("renders the tiptap editor in the workspace composer", () => {
  renderComposer();
  expect(screen.getByRole("textbox", { name: "邮件正文" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "插入表格" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "HTML 预览" })).toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm test -- WorkspaceComposerDockCopy.test.tsx WorkspacePageNextStep.test.tsx`

预期：FAIL，工作区仍使用旧编辑器。

- [ ] **步骤 3：替换工作区正文编辑器**

在 `frontend/src/components/organisms/WorkspaceComposerDock.tsx`：

- 删除：

```tsx
import { RichEmailEditor, type RichEmailValue } from "@/components/molecules/RichEmailEditor";
```

- 新增：

```tsx
import { EmailTemplateEditor } from "@/components/molecules/EmailTemplateEditor";
```

- 替换正文区域：

```tsx
<EmailTemplateEditor
  label="邮件正文"
  html={contentHtml}
  onChange={onContentChange}
/>
```

- [ ] **步骤 4：调整 `WorkspacePage` 状态流**

在 `frontend/src/pages/WorkspacePage.tsx` 保留 `contentHtml + content` 双字段，但确保：

```tsx
const handleContentChange = useCallback((value: { html: string; text: string }) => {
  setContent(value.text);
  setContentHtml(value.html);
}, []);
```

并继续在发送 payload 中提交：

```tsx
body_text: preparedBodyText,
body_html: contentHtml,
```

- [ ] **步骤 5：运行测试验证通过**

运行：`cd frontend && npm test -- WorkspaceComposerDockCopy.test.tsx WorkspacePageNextStep.test.tsx`

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add frontend/src/components/organisms/WorkspaceComposerDock.tsx frontend/src/pages/WorkspacePage.tsx frontend/test/WorkspaceComposerDockCopy.test.tsx frontend/test/WorkspacePageNextStep.test.tsx
git commit -m "feat(frontend): use tiptap editor in workspace composer"
```

## 任务 6：HTML 保真和样式回归

**文件：**
- 修改：`frontend/src/lib/richEmail.ts`
- 修改：`frontend/test/richEmail.test.ts`
- 修改：`frontend/test/EmailTemplateEditor.test.tsx`

- [ ] **步骤 1：编写失败的表格/字体保真测试**

在 `frontend/test/EmailTemplateEditor.test.tsx` 增加：

```tsx
it("does not drop table structure or font styles after one local edit", () => {
  const handleChange = vi.fn();

  render(
    <EmailTemplateEditor
      label="邮件正文"
      html='<table style="font-family:SimSun"><tbody><tr><td style="font-family:SimSun">老师您好</td></tr></tbody></table>'
      onChange={handleChange}
    />,
  );

  const editor = screen.getByRole("textbox", { name: "邮件正文" });
  editor.innerHTML =
    '<table style="font-family:SimSun"><tbody><tr><td style="font-family:SimSun">老师您好A</td></tr></tbody></table>';
  fireEvent.input(editor);

  expect(handleChange).toHaveBeenLastCalledWith({
    html: expect.stringContaining("<table"),
    text: expect.stringContaining("老师您好A"),
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm test -- EmailTemplateEditor.test.tsx richEmail.test.ts`

预期：FAIL，保真策略尚未对 Tiptap 输出和工具栏样式进行回归约束。

- [ ] **步骤 3：收紧 `richEmail` 对邮件 HTML 的处理**

在 `frontend/src/lib/richEmail.ts` 中保留：

- `table/tbody/tr/td/th`
- `font-family`
- `font-size`
- `line-height`
- `text-indent`
- `align/cellpadding/cellspacing/colspan/rowspan/style`

必要时增加一个辅助函数：

```ts
export const preserveEmailHtmlDuringEditing = (value: string): string => value.trim();
```

让 `EmailTemplateEditor` 编辑阶段优先保留 HTML 原貌，发送前再交给后端做最终清洗。

- [ ] **步骤 4：运行测试验证通过**

运行：`cd frontend && npm test -- EmailTemplateEditor.test.tsx richEmail.test.ts`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/lib/richEmail.ts frontend/test/richEmail.test.ts frontend/test/EmailTemplateEditor.test.tsx
git commit -m "fix(frontend): preserve email html during editing"
```

## 任务 7：全量前端验证

**文件：**
- 修改：无

- [ ] **步骤 1：运行前端关键测试**

运行：

```bash
cd frontend
npm test -- EmailTemplateEditor.test.tsx richEmail.test.ts ProfilePageOnboarding.test.tsx TestComposePage.test.tsx WorkspaceComposerDockCopy.test.tsx WorkspacePageNextStep.test.tsx
```

预期：PASS，全部测试文件通过。

- [ ] **步骤 2：运行前端 lint**

运行：`cd frontend && npm run lint`

预期：PASS，无 eslint error。

- [ ] **步骤 3：运行前端构建**

运行：`cd frontend && npm run build`

预期：PASS，输出包含 `✓ built`。

- [ ] **步骤 4：检查工作区差异**

运行：`git status --short`

预期：只剩本计划涉及的前端文件；没有 `node_modules`、`dist`、临时产物。

- [ ] **步骤 5：Commit**

```bash
git add docs/superpowers/plans/2026-04-24-tiptap-email-editor.md
git commit -m "docs: add tiptap email editor plan"
```
