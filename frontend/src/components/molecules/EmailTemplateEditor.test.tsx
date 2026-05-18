import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EmailTemplateEditor } from "./EmailTemplateEditor";

const createFileDropEvent = (file: File) => ({
  dataTransfer: {
    files: [file],
    types: ["Files"],
  },
});

describe("EmailTemplateEditor", () => {
  it("routes dropped template files to onFileDrop and prevents default editor insertion", () => {
    const onFileDrop = vi.fn();
    const droppedFile = new File(["模板内容"], "template.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });

    render(
      <EmailTemplateEditor
        label="默认模板正文"
        html=""
        onChange={vi.fn()}
        onFileDrop={onFileDrop}
      />,
    );

    const editor = screen.getByRole("textbox", { name: "默认模板正文" });
    document.elementFromPoint = vi.fn(() => editor);
    const dropEvent = createFileDropEvent(droppedFile);

    expect(fireEvent.drop(editor, dropEvent)).toBe(false);

    expect(onFileDrop).toHaveBeenCalledOnce();
    expect(onFileDrop).toHaveBeenCalledWith(droppedFile);
  });

  it("shows the placeholder only while the editor is empty", () => {
    const { rerender } = render(
      <EmailTemplateEditor
        label="默认模板正文"
        html=""
        placeholder="可将套磁信docx拖到此处导入"
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByText("可将套磁信docx拖到此处导入")).toBeInTheDocument();

    rerender(
      <EmailTemplateEditor
        label="默认模板正文"
        html="<p>已有正文</p>"
        placeholder="可将套磁信docx拖到此处导入"
        onChange={vi.fn()}
      />,
    );

    expect(screen.queryByText("可将套磁信docx拖到此处导入")).not.toBeInTheDocument();
  });
});
