// @vitest-environment happy-dom
import type { Node } from "@tiptap/pm/model";
import { createDocument, getSchema, type JSONContent } from "@tiptap/react";
import { describe, expect, test } from "vitest";
import { letterFormats } from "@/components/letter-editor";
import { toHtml, toMarkdown } from "@/markdown";

const schema = getSchema([letterFormats]);
const read = (markdown: string) => createDocument(toHtml(markdown), schema);

type Mark = NonNullable<JSONContent["marks"]>[number];

const doc = (...content: JSONContent[]) => schema.nodeFromJSON({ type: "doc", content });
const p = (...content: JSONContent[]): JSONContent => ({ type: "paragraph", content });
const t = (text: string, ...marks: (string | Mark)[]): JSONContent => ({
  type: "text",
  text,
  marks: marks.map((m) => (typeof m === "string" ? { type: m } : m)),
});
const br: JSONContent = { type: "hardBreak" };
const link = (href: string): Mark => ({ type: "link", attrs: { href } });
const li = (...content: JSONContent[]): JSONContent => ({ type: "listItem", content });
const bullets = (...items: JSONContent[]): JSONContent => ({ type: "bulletList", content: items });
const numbers = (start: number, ...items: JSONContent[]): JSONContent => ({
  type: "orderedList",
  attrs: { start },
  content: items,
});

const reread = (letter: Node) => read(toMarkdown(letter)).toJSON();

describe("a letter reads back as written", () => {
  test.each([
    ["text that looks like Markdown", doc(p(t("# 研究计划")), p(t("> 引用")), p(t("---")), p(t("甲"), br, t("===")))],
    ["text that looks like a list", doc(p(t("1. 方向甲")), p(t("3) 方向丙")), p(t("- 横线")), p(t("+ 加号")))],
    ["a list-like line after a break", doc(p(t("甲"), br, t("- 乙"), br, t("1. 丙")))],
    ["a bare address", doc(p(t("https://example.com/my_cv.pdf")))],
    ["Markdown and HTML characters", doc(p(t("2*3*4 a_b_c \\ <b>x</b> &amp; &#35; `code` ~del~ [x](y)")))],
    ["stars next to bold", doc(p(t("**"), t("粗", "bold"), t("**")))],
    ["two breaks in a row", doc(p(t("甲"), br, br, t("乙")))],
    ["a Chinese indent", doc(p(t("　　尊敬的老师：")), p(t("　　您好！")))],
    ["bold ending in Chinese punctuation", doc(p(t("你好，", "bold"), t("世界")))],
    ["italic in Chinese brackets", doc(p(t("（研究）", "italic"), t("方向")))],
    ["bold inside a word", doc(p(t("un"), t("believ", "bold"), t("able")))],
    ["crossing bold and italic", doc(p(t("甲", "bold"), t("乙", "bold", "italic"), t("丙", "italic")))],
    ["crossing italic and bold", doc(p(t("甲", "italic"), t("乙", "bold", "italic"), t("丙", "bold"), t("丁")))],
    [
      "a link with bold inside",
      doc(p(t("看", link("https://a.com/x_y")), t("这里", link("https://a.com/x_y"), "bold"))),
    ],
    ["a link inside bold", doc(p(t("前", "bold"), t("链", "bold", link("https://a.com")), t("后", "bold")))],
    ["a placeholder in bold", doc(p(t("{导师姓名}", "bold"), t("老师")))],
    ["nested lists", doc(bullets(li(p(t("一")), numbers(1, li(p(t("甲"))), li(p(t("乙"))))), li(p(t("二")))))],
    [
      "list items with paragraphs and breaks",
      doc(numbers(3, li(p(t("三")), p(t("续"))), li(p(t("四"), br, t("换行"))))),
    ],
  ])("%s", (_, letter) => {
    expect(reread(letter)).toEqual(letter.toJSON());
  });

  test("a link keeps its text when its address has brackets and spaces", () => {
    const letter = doc(p(t("链接", link("https://example.com/search?q=(a) b"))));
    expect(reread(letter)).toEqual(doc(p(t("链接", link("https://example.com/search?q=%28a%29%20b")))).toJSON());
  });

  test("what shows nothing is not kept", () => {
    const letter = doc(p(t("甲"), br, t(" ")), { type: "paragraph" }, p(t("加粗 ", "bold"), t("乙"), br));
    expect(toMarkdown(letter)).toBe("甲 \n\n**加粗** 乙");
  });
});

test("bold and italic written next to Chinese punctuation count", () => {
  expect(read("**你好，**世界 *（研究）*方向").toJSON()).toEqual(
    doc(p(t("你好，", "bold"), t("世界 "), t("（研究）", "italic"), t("方向"))).toJSON(),
  );
});
