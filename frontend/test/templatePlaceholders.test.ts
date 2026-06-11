import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  getTemplatePlaceholder,
  areTemplatePlaceholderHtmlEquivalent,
  parseTemplatePlaceholderText,
  prepareTemplatePlaceholderHtml,
  serializeTemplatePlaceholderHtml,
} from "@/lib/templatePlaceholders";
import {
  normalizeFontSizeStyle,
  normalizeFontSizeValue,
} from "@/lib/fontSize";

describe("templatePlaceholders", () => {
  it("finds known placeholders and ignores unknown keys", () => {
    expect(getTemplatePlaceholder("name")?.label).toBe("导师姓名");
    expect(getTemplatePlaceholder("year")?.label).toBe("发送年份");
    expect(getTemplatePlaceholder("month")?.token).toBe("{{month}}");
    expect(getTemplatePlaceholder("day")?.label).toBe("发送日期");
    expect(getTemplatePlaceholder("unknown")).toBeUndefined();
    expect(getTemplatePlaceholder(null)).toBeUndefined();
  });

  it("parses text into ordered text and placeholder segments with whitespace-tolerant tokens", () => {
    expect(parseTemplatePlaceholderText("您好 {{ name }}，我是{{sender_name}}。")).toEqual([
      { type: "text", value: "您好 " },
      { type: "placeholder", key: "name", label: "导师姓名", token: "{{name}}" },
      { type: "text", value: "，我是" },
      { type: "placeholder", key: "sender_name", label: "发件人姓名", token: "{{sender_name}}" },
      { type: "text", value: "。" },
    ]);
  });

  it("keeps unknown tokens as normal text while parsing", () => {
    expect(parseTemplatePlaceholderText("{{name}} 与 {{unknown}}")).toEqual([
      { type: "placeholder", key: "name", label: "导师姓名", token: "{{name}}" },
      { type: "text", value: " 与 {{unknown}}" },
    ]);
  });

  it("prepares and serializes placeholder spans without changing surrounding html", () => {
    const prepared = prepareTemplatePlaceholderHtml(
      "<p>{{ name }}老师您好，我是{{sender_email}}。{{year}}年{{month}}月{{day}}日</p>",
    );

    expect(prepared).toBe(
      '<p><span data-template-placeholder="name">导师姓名</span>老师您好，我是<span data-template-placeholder="sender_email">发件邮箱</span>。<span data-template-placeholder="year">发送年份</span>年<span data-template-placeholder="month">发送月份</span>月<span data-template-placeholder="day">发送日期</span>日</p>',
    );
    expect(serializeTemplatePlaceholderHtml(prepared)).toBe(
      "<p>{{name}}老师您好，我是{{sender_email}}。{{year}}年{{month}}月{{day}}日</p>",
    );
  });

  it("treats editor placeholder chips and stored template tokens as equivalent html", () => {
    expect(
      areTemplatePlaceholderHtmlEquivalent(
        "<p>{{name}}老师您好</p>",
        '<p><span data-template-placeholder="name" class="email-placeholder-chip" data-token="{{name}}">导师姓名</span>老师您好</p>',
      ),
    ).toBe(true);
  });

  it("treats browser-normalized style formatting as equivalent html", () => {
    expect(
      areTemplatePlaceholderHtmlEquivalent(
        '<p style="text-align: left; line-height: 1.5; text-indent: 0px; font-family: 宋体; font-size: 12pt;">模板正文</p>',
        '<p style="font-size:12pt;font-family:宋体;text-indent:0px;line-height:1.5;text-align:left;">模板正文</p>',
      ),
    ).toBe(true);
  });

  it("keeps placeholder chips inheriting font weight so surrounding bold text still shows", () => {
    const css = readFileSync(join(process.cwd(), "src/index.css"), "utf8");
    const chipRule = css.match(/\.email-placeholder-chip\s*\{([\s\S]*?)\}/)?.[1] ?? "";

    expect(chipRule).toContain("color: inherit;");
    expect(chipRule).toContain("font-size: inherit;");
    expect(chipRule).toContain("font-weight: inherit;");
    expect(chipRule).toContain("line-height: inherit;");
    expect(chipRule).not.toContain("color: rgb(153 27 27);");
    expect(chipRule).not.toContain("font-size: 0.8125rem;");
    expect(chipRule).not.toContain("font-weight: 600;");
    expect(chipRule).not.toContain("line-height: 1.45;");
  });

  it("normalizes legacy relative font sizes to absolute sizes", () => {
    expect(normalizeFontSizeValue("+1")).toBe("14pt");
    expect(normalizeFontSizeValue("-1")).toBe("10pt");
    expect(normalizeFontSizeValue("+4")).toBe("36pt");
    expect(normalizeFontSizeStyle("font-size:+1;color:#333333")).toBe("font-size:14pt;color:#333333;");
  });
});
