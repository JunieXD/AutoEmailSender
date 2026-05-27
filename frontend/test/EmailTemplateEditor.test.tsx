import { fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { EmailTemplateEditor } from "@/components/molecules/EmailTemplateEditor";
import {
  emailTemplateEditorDisablesInlineMarkdownInputRules,
  emailTemplateEditorDisablesStructuralInputRules,
} from "@/components/molecules/tiptap/listExtensions";
import { prepareTemplateEditorHtml } from "@/lib/templatePlaceholders";

describe("EmailTemplateEditor", () => {
  it("renders the editor and toolbar controls", () => {
    render(
      <EmailTemplateEditor
        label="默认模板正文"
        html="<p>老师您好</p>"
        onChange={vi.fn()}
      />,
    );

    const editor = screen.getByRole("textbox", { name: "默认模板正文" });
    expect(editor).toBeInTheDocument();
    expect(editor).toHaveClass("max-h-[520px]", "overflow-y-auto", "overscroll-contain");
    expect(screen.getByRole("button", { name: "加粗" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "插入表格" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "HTML 预览" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "字体菜单" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "字号菜单" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "行距菜单" })).toHaveTextContent("行距");
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("offers expanded typography choices with readable labels", () => {
    render(
      <EmailTemplateEditor
        label="邮件正文"
        html='<p style="line-height:1.5">老师您好</p>'
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "行距菜单" })).toHaveTextContent("1.5 倍行距");

    fireEvent.click(screen.getByRole("button", { name: "字号菜单" }));
    expect(screen.getByRole("button", { name: "10pt" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "22pt" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "字体菜单" }));
    expect(screen.getByRole("button", { name: "黑体" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Arial" })).toBeInTheDocument();
  });

  it("shows table editing actions when editing a table", () => {
    render(
      <EmailTemplateEditor
        label="邮件正文"
        html="<p>老师您好</p>"
        onChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "插入表格" }));

    expect(screen.getByRole("group", { name: "表格操作" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上方插入行" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "右侧插入列" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除表格" })).toBeInTheDocument();
  });

  it("inserts regular table cells so table text matches body typography", () => {
    const handleChange = vi.fn();
    render(
      <EmailTemplateEditor
        label="邮件正文"
        html="<p>老师您好</p>"
        onChange={handleChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "插入表格" }));

    const latestHtml = handleChange.mock.lastCall?.[0].html as string;
    expect(latestHtml).toContain("<table");
    expect(latestHtml).toContain("<td");
    expect(latestHtml).not.toContain("<th");
  });

  it("prevents pasted table cell font sizes from overriding editor typography", () => {
    const editorCss = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8");

    expect(editorCss).toMatch(
      /\.email-editor-content :where\(th, td\) \{[\s\S]*font-size: inherit !important;/,
    );
    expect(editorCss).toMatch(
      /\.email-editor-content :where\(th, td\) > \* \{[\s\S]*font-size: inherit !important;/,
    );
  });

  it("shows generated list markers in the email editor", () => {
    const editorCss = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8");

    render(
      <EmailTemplateEditor
        label="邮件正文"
        html="<ol><li><p>我就读于 xxx 大学</p></li><li><p>我对您的研究很感兴趣</p></li></ol>"
        onChange={vi.fn()}
      />,
    );

    const editor = screen.getByRole("textbox", { name: "邮件正文" });
    expect(editor).toHaveClass("email-editor-content");
    expect(editor.querySelector("ol li")).not.toBeNull();
    expect(editorCss).toMatch(
      /\.email-editor-content :where\(ol\) \{[\s\S]*list-style-type: decimal;/,
    );
    expect(editorCss).toMatch(
      /\.email-editor-content :where\(ul\) \{[\s\S]*list-style-type: disc;/,
    );
    expect(editorCss).toMatch(
      /\.email-editor-content :where\(li\) \{[\s\S]*padding-left: 0\.25rem;/,
    );
  });

  it("keeps toolbar list creation available after disabling typed list shortcuts", () => {
    const handleChange = vi.fn();

    render(
      <EmailTemplateEditor
        label="邮件正文"
        html="<p>我就读于 xxx 大学</p>"
        onChange={handleChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "有序列表" }));
    expect(handleChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        html: expect.stringContaining("<ol>"),
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "无序列表" }));
    expect(handleChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        html: expect.stringContaining("<ul>"),
      }),
    );
  });

  it("does not register typed shortcuts that convert text into block structures", () => {
    expect(emailTemplateEditorDisablesStructuralInputRules()).toBe(true);
  });

  it("does not register Markdown inline shortcuts for text marks", () => {
    expect(emailTemplateEditorDisablesInlineMarkdownInputRules()).toBe(true);
  });
  it("renders known template tokens as inline placeholder chips", () => {
    render(
      <EmailTemplateEditor
        label="邮件正文"
        html="<p>{{name}}老师您好，我是{{sender_name}}。{{year}}年{{month}}月{{day}}日</p>"
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByText("导师姓名")).toBeInTheDocument();
    expect(screen.getByText("发件人姓名")).toBeInTheDocument();
    expect(screen.getByText("发送年份")).toBeInTheDocument();
    expect(screen.getByText("发送月份")).toBeInTheDocument();
    expect(screen.getByText("发送日期")).toBeInTheDocument();
    expect(screen.queryByText("{{name}}")).not.toBeInTheDocument();
  });

  it("keeps imported word heading structure in the editor", () => {
    render(
      <EmailTemplateEditor
        label="邮件正文"
        html='<h1 style="font-size:16pt;line-height:1.5">[推免自荐] 陈帆</h1><p style="text-indent:2em">正文</p>'
        onChange={vi.fn()}
      />,
    );

    const editor = screen.getByRole("textbox", { name: "邮件正文" });

    expect(editor.querySelector("h1")).not.toBeNull();
    expect(editor.innerHTML).toContain("font-size: 16pt");
    expect(editor.innerHTML).toContain("line-height: 1.5");
    expect(editor.innerHTML).toContain("text-indent: 2em");
  });

  it("keeps font tags as editable typography styles in the editor", () => {
    render(
      <EmailTemplateEditor
        label="邮件正文"
        html='<p><font face="宋体" color="#333333" size="3">老师您好</font></p>'
        onChange={vi.fn()}
      />,
    );

    const editor = screen.getByRole("textbox", { name: "邮件正文" });

    expect(editor.innerHTML).toContain("font-family");
    expect(editor.innerHTML).toContain("宋体");
    expect(editor.innerHTML).toContain("font-size");
    expect(editor.innerHTML).toContain("12pt");
    expect(editor.innerHTML).toContain("color");
    expect(editor.innerHTML).toContain("rgb(51, 51, 51)");
  });

  it("normalizes legacy relative font sizes from font tags and inline styles", () => {
    const preparedHtml = prepareTemplateEditorHtml(
      '<p><font face="宋体" color="#333333" size="+1">老师您好</font><span style="font-size:-1">同学你好</span></p>',
    );

    expect(preparedHtml).toContain("font-size:14pt");
    expect(preparedHtml).toContain("font-size:10pt");
    expect(preparedHtml).not.toContain("font-size:+1");
    expect(preparedHtml).not.toContain("font-size:-1");

    render(
      <EmailTemplateEditor label="邮件正文" html={preparedHtml} onChange={vi.fn()} />,
    );

    const editor = screen.getByRole("textbox", { name: "邮件正文" });
    expect(editor.innerHTML).toContain("font-size: 14pt");
    expect(editor.innerHTML).toContain("font-size: 10pt");
  });

  it("inserts placeholder chips and emits template tokens", () => {
    const handleChange = vi.fn();
    render(
      <EmailTemplateEditor
        label="邮件正文"
        html="<p>老师您好</p>"
        onChange={handleChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "占位符菜单" }));
    expect(screen.getByRole("button", { name: "发送年份" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "发送月份" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "发送日期" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "发送日期" }));

    expect(screen.getByText("发送日期")).toBeInTheDocument();
    expect(handleChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        html: expect.stringContaining("{{day}}"),
        text: expect.stringContaining("{{day}}"),
      }),
    );
  });

  it("renders the placeholder menu in a fixed portal to avoid clipped workspace panels", () => {
    render(
      <EmailTemplateEditor
        label="邮件正文"
        html="<p>老师您好</p>"
        onChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "占位符菜单" }));

    const menu = screen.getByTestId("email-template-placeholder-menu");
    expect(menu.parentElement).toBe(document.body);
    expect(menu).toHaveClass("fixed");
    expect(menu).toHaveClass("z-[80]");
  });
});
