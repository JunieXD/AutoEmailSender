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
    expect(notes).toContain("Windows：下载 `AutoEmailSender Setup 2.0.2.exe` 后双击安装。");
    expect(notes).toContain("macOS Apple Silicon：下载 `AutoEmailSender-2.0.2-arm64.dmg`，打开后把应用拖到“应用程序”。");
    expect(notes).toContain("系统设置 > 隐私与安全性");
    expect(notes).toContain("Intel Mac 暂未提供安装包。");
    expect(notes).toContain("请只从本项目 GitHub Releases 页面下载安装包。");
    expect(notes).toContain("Windows：应用内可下载并安装更新。");
    expect(notes).toContain("macOS Apple Silicon：应用内可检查更新；发现新版本后会打开 GitHub Releases，请下载新版 `.dmg` 并拖到“应用程序”覆盖安装。");
    expect(notes).not.toContain("fix(后端)");
    expect(notes).not.toContain("AutoEmailSender-Setup-2.0.2.exe");
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
