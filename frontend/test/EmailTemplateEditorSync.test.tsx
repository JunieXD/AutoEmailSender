import React, { useState } from "react";
import { act, render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const setContent = vi.fn();
const setEditable = vi.fn();
let currentEditorHtml = "";
let latestEditorOptions: {
  onUpdate?: (payload: {
    editor: typeof editor;
    transaction?: { getMeta: (key: string) => unknown };
  }) => void;
} | null = null;

const editor = {
  commands: {
    setContent,
  },
  setEditable,
  getAttributes: vi.fn(() => ({})),
  getHTML: vi.fn(() => currentEditorHtml),
  isActive: vi.fn(() => false),
  isFocused: true,
  chain: vi.fn(() => ({
    focus: vi.fn().mockReturnThis(),
    toggleBold: vi.fn().mockReturnThis(),
    toggleItalic: vi.fn().mockReturnThis(),
    toggleUnderline: vi.fn().mockReturnThis(),
    setLink: vi.fn().mockReturnThis(),
    insertTable: vi.fn().mockReturnThis(),
    insertTemplatePlaceholder: vi.fn().mockReturnThis(),
    setMark: vi.fn().mockReturnThis(),
    updateAttributes: vi.fn().mockReturnThis(),
    run: vi.fn(),
  })),
};

vi.mock("@tiptap/react", () => ({
  useEditor: (options: {
    onUpdate?: (payload: {
      editor: typeof editor;
      transaction?: { getMeta: (key: string) => unknown };
    }) => void;
  }) => {
    latestEditorOptions = options;
    return editor;
  },
  EditorContent: () => <div data-testid="mock-editor" />,
}));

const { EmailTemplateEditor } = await import("@/components/molecules/EmailTemplateEditor");

const ControlledEditor = () => {
  const [html, setHtml] = useState('<p><span style="font-size: 12pt">老师您好</span></p>');

  return (
    <EmailTemplateEditor
      label="邮件正文"
      html={html}
      onChange={({ html: nextHtml }) => setHtml(nextHtml)}
    />
  );
};

describe("EmailTemplateEditor local sync", () => {
  beforeEach(() => {
    setContent.mockClear();
    setEditable.mockClear();
    latestEditorOptions = null;
    currentEditorHtml = '<p><span style="font-size: 12pt">老师您好</span></p>';
  });

  it("does not reset editor content when the parent echoes a local update", async () => {
    render(<ControlledEditor />);
    setContent.mockClear();

    currentEditorHtml = '<p><span style="font-size: 12pt">老师您好A</span></p>';
    await act(async () => {
      latestEditorOptions?.onUpdate?.({
        editor,
        transaction: { getMeta: () => "input" },
      });
    });

    expect(setContent).not.toHaveBeenCalled();
  });
});
