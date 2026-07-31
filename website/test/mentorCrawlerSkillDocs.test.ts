import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const guide = readFileSync(resolve("docs/mentor-crawler-skill.md"), "utf8");

describe("mentor crawler Skill guide", () => {
  it("provides paste-ready global installation for ordinary users", () => {
    expect(guide).toContain("你不需要下载 Auto Email Sender 源码");
    expect(guide).toContain("请使用 $skill-installer");
    expect(guide).toContain("master 分支");
    expect(guide).toContain("$HOME/.agents/skills");
    expect(guide).toContain("~/.claude/skills/crawl-mentors-to-xlsx");
    expect(guide).toContain("不要只下载 SKILL.md");
    expect(guide).toContain("安装后的 Skill 不会随 Auto Email Sender 桌面应用自动更新");
    expect(guide).toContain("scripts/build_professors_xlsx.py");
    expect(guide).toContain("scripts/validate_professors_xlsx.py");
    expect(guide).toContain("assets/professor-import-contract.v1.json");
    expect(guide).toContain("分别运行两个 Python 脚本的 --help");
    expect(guide).toContain("只读到 `SKILL.md` 还不够");
    expect(guide).toContain("手动安装 Release ZIP");
    expect(guide).toContain("crawl-mentors-to-xlsx-vX.Y.Z.zip");
    expect(guide).toContain("%USERPROFILE%\\.agents\\skills\\");
    expect(guide).toContain("%USERPROFILE%\\.claude\\skills\\");
    expect(guide).toContain("不能多套一层同名目录");
    expect(guide).toContain("不要下载 GitHub 自动生成的整个项目源码压缩包");
  });

  it("explains why the safe workbook omits user-owned fields", () => {
    expect(guide).toContain("默认只有 10 列");
    expect(guide).toContain("保留系统中的原标签");
    expect(guide).toContain("保留系统中的原备注");
    expect(guide).toContain("完整 12 列");
  });
});
