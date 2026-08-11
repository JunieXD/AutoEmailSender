import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const guide = readFileSync(resolve("docs/mentor-crawler-skill.md"), "utf8");

describe("mentor crawler Skill guide", () => {
  it("provides paste-ready global installation for ordinary users", () => {
    expect(guide).toContain("无需下载 Auto Email Sender 源码或编写爬虫");
    expect(guide).toContain("请使用 $skill-installer");
    expect(guide).toContain("master 分支");
    expect(guide).toContain("~/.agents/skills/");
    expect(guide).toContain("~/.claude/skills/crawl-mentors-to-xlsx");
    expect(guide).toContain("不要只下载 SKILL.md");
    expect(guide).toContain("桌面应用不会自动更新 Skill");
    expect(guide).toContain("scripts/build_professors_xlsx.py");
    expect(guide).toContain("scripts/validate_professors_xlsx.py");
    expect(guide).toContain("assets/professor-import-contract.v1.json");
    expect(guide).toContain("分别运行两个 Python 脚本的 --help");
    expect(guide).toContain("才表示安装完整");
    expect(guide).toContain("手动安装 Release ZIP");
    expect(guide).toContain("crawl-mentors-to-xlsx-vX.Y.Z.zip");
    expect(guide).toContain("%USERPROFILE%\\.agents\\skills\\");
    expect(guide).toContain("%USERPROFILE%\\.claude\\skills\\");
    expect(guide).toContain("不要多套一层同名目录");
    expect(guide).toContain("不要下载 GitHub 自动生成的源码压缩包");
  });

  it("explains why the safe workbook omits user-owned fields", () => {
    expect(guide).toContain("默认导出 10 列的原因");
    expect(guide).toContain("保留原标签");
    expect(guide).toContain("保留原备注");
    expect(guide).toContain("完整 12 列");
  });
});
