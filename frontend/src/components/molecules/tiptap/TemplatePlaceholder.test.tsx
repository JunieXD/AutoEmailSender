import { Editor } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import TextStyle from "@tiptap/extension-text-style";
import { describe, expect, it } from "vitest";
import { FontFamily } from "./FontFamily";
import { FontSize } from "./FontSize";
import { TemplatePlaceholder } from "./TemplatePlaceholder";
import { TextColor } from "./TextColor";

const createEditor = () =>
  new Editor({
    extensions: [
      StarterKit,
      TextStyle,
      TemplatePlaceholder,
      FontFamily,
      FontSize,
      TextColor,
    ],
    content: "<p></p>",
  });

describe("TemplatePlaceholder", () => {
  it("keeps the active text style when inserting a placeholder", () => {
    const editor = createEditor();

    editor
      .chain()
      .focus()
      .setMark("textStyle", {
        fontFamily: "Arial, sans-serif",
        fontSize: "18pt",
        color: "rgb(10, 20, 30)",
      })
      .insertContent("Hello ")
      .insertTemplatePlaceholder("name")
      .run();

    let foundPlaceholder = false;
    let placeholderMarks: Record<string, unknown> | null = null;
    editor.state.doc.descendants((node) => {
      if (node.type.name !== "templatePlaceholder") {
        return true;
      }
      foundPlaceholder = true;
      placeholderMarks = node.marks.find((mark) => mark.type.name === "textStyle")?.attrs ?? null;
      return false;
    });

    expect(foundPlaceholder).toBe(true);
    expect(placeholderMarks).toMatchObject({
      fontFamily: "Arial, sans-serif",
      fontSize: "18pt",
      color: "rgb(10, 20, 30)",
    });
  });
});
