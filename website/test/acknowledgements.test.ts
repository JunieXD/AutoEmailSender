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
    expect(page).toContain("outline: [3, 3]");
    expect(page).toContain("羽华丶");
    expect(page).toContain(
      'class="supporter-entry__avatar" src="https://q1.qlogo.cn/g?b=qq&amp;nk=1136870663&amp;s=100"',
    );
    expect(page.match(/class="supporter-entry__avatar"/g)).toHaveLength(50);
    expect(page.match(/<article class="supporter-entry"/g)).toHaveLength(50);
    expect(page.match(/<ul>/g)).toHaveLength(50);
    expect(page.match(/<section class="acknowledgement-group"/g)).toHaveLength(1);
    expect(page).toContain(
      "名单根据 2026 年 6 月至 8 月 QQ 群聊中可核实的项目反馈整理，不代表贡献排序。",
    );
    expect(page).not.toContain("supporter-entry__qq");
    expect(page).not.toContain("(QQ:");
    expect(page).toContain(
      "提供中转站 GPT 模型 $800 额度，用于项目开发与测试。",
    );
    expect(page).not.toContain("将额度用于项目开发与测试");
    expect(page).toContain(
      "提供中山大学、复旦大学、上海交通大学、中国科学技术大学、哈尔滨工业大学、西安交通大学等院校的相关学院教师页面用于抓取测试，并反馈导师数量不足、研究方向或职称等字段提取不完整，以及信息补全后仍有字段缺失的问题。",
    );
    expect(page).toContain(
      "建议导出无邮箱、无研究方向及不符合导入标准的导师，并支持补全后批量重试。",
    );
    expect(page).not.toContain("建议新增补全后可以批量重试的功能。");
    expect(page).not.toContain("建议补全后批量重试</li>");
    expect(page).toContain("疯狂轮指八度音");
    expect(page).toContain("奇华");
    expect(page).toContain("pretty");
    expect(page).toContain("hygge");
    expect(page).toContain("woodfish");
    expect(page).toContain("青青草原领头羊");
    expect(page).toContain(">#define</h3>");
    expect(page).toContain(">！</h3>");
    expect(page).toContain(">。</h3>");
    expect(page).toContain(
      "建议新增复用本地 Codex 或 Claude Code 的模型配置方式",
    );
    expect(page).toContain("反馈 macOS 安装包打开时提示文件损坏");
    expect(page).toContain("建议新增导师分组功能");
    expect(page).toContain("社区贡献者");
    expect(page).not.toContain("额度与基础支持");
    expect(page).not.toContain("功能建议与问题反馈");
    expect(page).not.toContain("导师数据与抓取测试");
    expect(page).not.toContain("邮件与配置测试");
    expect(page).toContain(">“⠀”</h3>");
    expect(page).not.toContain("空白昵称");
    expect(page.match(/<h3[^>]*>疯狂轮指八度音<\/h3>/g)).toHaveLength(1);
    expect(page.match(/<h3[^>]*>奇华<\/h3>/g)).toHaveLength(1);
    expect(page.match(/<h3[^>]*>k<\/h3>/g)).toHaveLength(1);
    expect(page).not.toContain("记录原则");
    expect(page).not.toContain("捐赠 GPT 模型调用额度");
    expect(page).not.toContain("个人主页链接");
    expect(page).not.toContain("supporter-entry__role");
    expect(page).not.toContain("supporter-entry__details");
    expect(page).not.toContain("<dt>");
    expect(page).not.toContain("模型额度支持");

    const entries = [
      ...page.matchAll(
        /<article class="supporter-entry"[\s\S]*?<h3[^>]*>([^<]+)<\/h3>[\s\S]*?<ul>([\s\S]*?)<\/ul>[\s\S]*?<\/article>/g,
      ),
    ];
    const contributionCounts = entries.map(
      ([, , contribution]) => contribution.match(/<li>/g)?.length ?? 0,
    );
    expect(contributionCounts).toContain(10);
    expect(contributionCounts).toContain(1);
    expect(Math.max(...contributionCounts)).toBeGreaterThan(
      Math.min(...contributionCounts),
    );

    const contributionTexts = [
      ...page.matchAll(/<li>([^<]+)<\/li>/g),
    ].map(([, text]) => text);
    expect(contributionTexts.every((text) => /[。？]$/.test(text))).toBe(true);

    const names = entries.map(([, name]) => name);
    expect(new Set(names).size).toBe(50);
    expect(names.filter((name) => name === "疯狂轮指八度音")).toHaveLength(1);
    expect(names.filter((name) => name === "奇华")).toHaveLength(1);
    expect(names.filter((name) => name === "k")).toHaveLength(1);

    const prettyContribution = entries.find(([, name]) => name === "pretty")?.[2];
    expect(prettyContribution?.match(/<li>/g)).toHaveLength(1);
  });

  it("is discoverable from primary navigation, help navigation, and the footer", () => {
    const config = readWebsiteFile(".vitepress/config.mts");

    expect(config).toContain('label: "页面导航"');
    expect(
      config.match(/\{ text: "致谢", link: "\/acknowledgements" \}/g),
    ).toHaveLength(2);
    expect(config).not.toContain(">查看致谢</a>");
  });
});
