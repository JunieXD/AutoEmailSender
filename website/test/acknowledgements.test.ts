import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const readWebsiteFile = (path: string) => readFileSync(resolve(path), "utf8");

describe("acknowledgements page", () => {
  it("records the approved public contribution details", () => {
    const page = readWebsiteFile("acknowledgements.md");

    expect(page).toContain(
      "Auto Email Sender 的持续开发离不开各位同学的支持。此处用于记录每一位贡献者的帮助，也感谢每一位提交反馈、完善数据和参与测试的用户。",
    );
    expect(page).toContain("羽华丶");
    expect(page).toContain("<dt>支持</dt>");
    expect(page).toContain("中转站 GPT 模型 $800 额度");
    expect(page).toContain("项目开发与测试");
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
