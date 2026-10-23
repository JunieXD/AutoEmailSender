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
