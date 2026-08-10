import assert from "node:assert/strict";
import test from "node:test";

import { buildPrereleaseNotes } from "./prerelease-notes.mjs";

test("prerelease notes require risk, backup, isolation, fallback, diagnostics, and supersession guidance", () => {
  const notes = buildPrereleaseNotes({ version: "2.6.0-beta.1", channel: "beta" });
  const headings = [
    "# v2.6.0-beta.1（Beta 测试版）",
    "## 测试版说明",
    "### 新增功能",
    "### 体验优化",
    "### 问题修复",
    "## 测试重点",
    "## 安装前备份",
    "## 安装与覆盖升级",
    "## 回退与诊断",
    "## 自动更新隔离",
    "## 停止使用与取代",
  ];
  let previousIndex = -1;
  for (const heading of headings) {
    const index = notes.indexOf(heading);
    assert.ok(index > previousIndex, `missing or out-of-order heading: ${heading}`);
    previousIndex = index;
  }
  assert.match(notes, /不会通过应用内“检查更新”提供/);
  assert.match(notes, /不会成为 GitHub Latest/);
  assert.match(notes, /不会自动上传/);
  assert.match(notes, /单进程兼容模式/);
  assert.match(notes, /不会自动重发/);
  assert.match(notes, /不发布稳定通道使用的 `latest\.yml`、`appcast\.xml`/);
  assert.match(notes, /AutoEmailSender-Setup-2\.6\.0-beta\.1\.exe/);
  assert.match(notes, /AutoEmailSender-2\.6\.0-beta\.1-arm64\.dmg/);
});
