// @vitest-environment happy-dom
import { expect, test } from "vitest";
import type { LetterStyle } from "@/api/types";
import { letterHtml } from "@/letter";

const style: LetterStyle = {
  font: null,
  fontSizePt: 10.5,
  lineHeight: 1.6,
  paragraphSpacingEm: 0.8,
  firstLineIndent: false,
};
const render = (markdown: string, s: LetterStyle) =>
  new DOMParser().parseFromString(letterHtml(markdown, s), "text/html").body;

test("the font is left to the recipient unless one is chosen", () => {
  expect(render("甲", style).querySelector("div")?.style.fontFamily).toBe("");
  expect(render("甲", { ...style, font: "song" }).querySelector("div")?.style.fontFamily).toContain("SimSun");
});

test("paragraphs are indented, list items are not", () => {
  const page = render("甲\n\n1. 乙\n\n   丙\n\n丁", { ...style, firstLineIndent: true });
  const indents = [...page.querySelectorAll("p")].map((p) => [p.textContent, p.style.textIndent]);
  expect(indents).toEqual([
    ["甲", "2em"],
    ["乙", ""],
    ["丙", ""],
    ["丁", "2em"],
  ]);
});

test("links to anything but web pages and email addresses are only text", () => {
  const page = render("[点我](javascript:alert(1)) [网页](https://a.com) [邮箱](mailto:a@b.com)", style);
  expect([...page.querySelectorAll("a")].map((a) => a.getAttribute("href"))).toEqual([
    "https://a.com",
    "mailto:a@b.com",
  ]);
  expect(page.textContent).toContain("点我");
});

test("pictures and task boxes stay as written", () => {
  const page = render("![简历](https://a.com/t.png)\n\n- [x] 做过的工作\n\n  补充说明", style);
  expect(page.querySelector("img, input")).toBeNull();
  expect([...page.querySelectorAll("p")].map((e) => e.textContent)).toEqual([
    "![简历](https://a.com/t.png)",
    "[x] 做过的工作",
    "补充说明",
  ]);
});

test("spaces show as typed", () => {
  const page = render("    王老师：\n\n张三    计算机专业\\\n  **large**  language **models** too", style);
  const shown = [...page.querySelectorAll("p")].map((p) => p.innerHTML);
  expect(shown).toEqual([
    "&nbsp;&nbsp;&nbsp;&nbsp;王老师：",
    "张三 &nbsp;&nbsp;&nbsp;计算机专业<br>&nbsp;&nbsp;<strong>large</strong> &nbsp;language <strong>models</strong> too",
  ]);
});
