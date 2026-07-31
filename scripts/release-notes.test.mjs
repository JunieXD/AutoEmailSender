import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { buildReleaseNotes, generateReleaseNotes } from "./release-notes.mjs";

describe("release notes generator", () => {
  it("creates a user-facing announcement template without commit subjects", () => {
    const notes = buildReleaseNotes("v2.0.2");

    expect(notes).toContain("# v2.0.2");
    expect(notes).toContain("## 更新内容");
    expect(notes).toContain("### 新增功能");
    expect(notes).toContain("### 体验优化");
    expect(notes).toContain("### 问题修复");
    expect(notes).toContain("Windows：下载 `AutoEmailSender-Setup-2.0.2.exe` 后双击安装。");
    expect(notes).toContain("macOS Apple Silicon：下载 `AutoEmailSender-2.0.2-arm64.dmg`，打开后把应用拖到“应用程序”。");
    expect(notes).toContain("macOS 版本尚未通过 Apple 官方认证，首次打开可能会被系统拦截");
    expect(notes).toContain("系统设置 > 隐私与安全性");
    expect(notes).toContain("Intel Mac 暂未提供安装包。");
    expect(notes).not.toContain("ad-hoc");
    expect(notes).not.toContain("Developer ID");
    expect(notes).not.toContain("Apple 公证");
    expect(notes).not.toContain("Gatekeeper");
    expect(notes).not.toContain("请只从本项目 GitHub Releases 页面下载安装包。");
    expect(notes).toContain("Windows：支持在应用内下载并安装更新。");
    expect(notes).toContain("macOS Apple Silicon：支持自动检查并在应用内安装更新。");
    expect(notes).toContain("旧版 macOS 用户需要手动安装本版本一次，之后即可使用应用内更新。");
    expect(notes).toContain("## 导师抓取Skill");
    expect(notes).not.toContain("## 从导师官网生成导入表");
    expect(notes).toContain(
      "[导师抓取Skill 安装与使用教程](https://juniexd.github.io/AutoEmailSender/docs/mentor-crawler-skill)",
    );
    expect(notes).toContain(
      "[`crawl-mentors-to-xlsx-v2.0.2.zip`](https://github.com/JunieXD/AutoEmailSender/releases/download/v2.0.2/crawl-mentors-to-xlsx-v2.0.2.zip)",
    );
    expect(notes.lastIndexOf("导师抓取Skill 安装与使用教程")).toBeGreaterThan(
      notes.lastIndexOf("## 自动更新"),
    );
    expect(notes).not.toContain("fix(后端)");
    expect(notes).not.toContain("AutoEmailSender Setup 2.0.2.exe");
  });

  it("writes the template to disk without reading git history", () => {
    const repoRoot = mkdtempSync(join(tmpdir(), "auto-email-sender-release-"));
    try {
      const outputPath = join(repoRoot, "release-notes.md");
      const notes = generateReleaseNotes({
        repoRoot,
        version: "v1.2.3",
        outputPath,
        runGitCommand: (_repoRoot, args) => {
          throw new Error(`unexpected git args: ${args.join(" ")}`);
        },
      });

      expect(notes).toContain("# v1.2.3");
      expect(notes).toContain("### 新增功能");
      expect(notes).toContain("### 体验优化");
      expect(notes).toContain("### 问题修复");
      expect(readFileSync(outputPath, "utf8")).toBe(notes);
    } finally {
      rmSync(repoRoot, { recursive: true, force: true });
    }
  });
});
