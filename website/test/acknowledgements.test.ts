import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const readWebsiteFile = (path: string) => readFileSync(resolve(path), "utf8");

describe("acknowledgements page", () => {
  it("records the approved public contribution details", () => {
    const page = readWebsiteFile("acknowledgements.md");

    expect(page).toContain(
      "感谢每一位提出建议、报告问题、参与复测、提供教师目录和完善导师数据的朋友。",
    );
    expect(page).toContain("羽华丶");
    expect(page).toContain("(QQ: 1136870663)");
    expect(page).toContain("(QQ: 2739509130)");
    expect(page).toContain("<dt>支持</dt>");
    expect(page).toContain("中转站 GPT 模型 $800 额度");
    expect(page).toContain("项目开发与测试");
    expect(page).toContain("疯狂轮指八度音");
    expect(page).toContain("奇华");
    expect(page).toContain("pretty");
    expect(page).toContain("hygge");
    expect(page).toContain("功能建议与问题反馈");
    expect(page).toContain("导师数据与抓取测试");
    expect(page).toContain("邮件与配置测试");
    expect(page).toContain(">“⠀”</h3>");
    expect(page).not.toContain("空白昵称");
    expect(page.match(/<h3[^>]*>疯狂轮指八度音<\/h3>/g)).toHaveLength(3);
    expect(page.match(/<h3[^>]*>奇华<\/h3>/g)).toHaveLength(3);
    expect(page.match(/<h3[^>]*>k<\/h3>/g)).toHaveLength(3);
    expect(page).not.toContain("记录原则");
    expect(page).not.toContain("捐赠 GPT 模型调用额度");
    expect(page).not.toContain("个人主页链接");
  });

  it("is discoverable from primary navigation, help navigation, and the footer", () => {
    const config = readWebsiteFile(".vitepress/config.mts");

    expect(
      config.match(/\{ text: "致谢", link: "\/acknowledgements" \}/g),
    ).toHaveLength(2);
    expect(config).not.toContain(">查看致谢</a>");
  });
});
