import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const editorSource = readFileSync(
  resolve(process.cwd(), "src/components/molecules/EmailTemplateEditor.tsx"),
  "utf8",
);

describe("EmailTemplateEditor", () => {
  it("prevents the editor default drop behavior when a template file is dropped", () => {
    expect(editorSource).toContain("onFileDrop?: (file: File) => void");
    expect(editorSource).toContain("handleDOMEvents");
    expect(editorSource).toContain("drop: (_view, event)");
    expect(editorSource).toContain("event.preventDefault()");
    expect(editorSource).toContain("onFileDropRef.current(file)");
  });

  it("supports an empty editor placeholder", () => {
    expect(editorSource).toContain("placeholder?: string");
    expect(editorSource).toContain("isEditorEmpty");
    expect(editorSource).toContain("placeholder && isEditorEmpty");
  });
});
