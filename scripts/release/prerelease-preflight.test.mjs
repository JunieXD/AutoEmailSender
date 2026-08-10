import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { buildPrereleaseNotes } from "./prerelease-notes.mjs";
import { assertPrereleasePreflight } from "./prerelease-preflight.mjs";

const version = "9.9.9-beta.1";
const channel = "beta";
const sourceBranch = "beta/test-topic";
const validBuilderConfig = "mac:\n  extendInfo:\n    SUFeedURL: https://github.com/example/repo/releases/latest/download/appcast.xml\npublish:\n  releaseType: release\n";

async function createFixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "prerelease-preflight-test-"));
  await Promise.all([
    mkdir(path.join(root, "cli"), { recursive: true }),
    mkdir(path.join(root, "desktop"), { recursive: true }),
    mkdir(path.join(root, "frontend"), { recursive: true }),
    mkdir(path.join(root, "docs", "releases"), { recursive: true }),
  ]);
  execFileSync("git", ["-C", root, "init"]);
  execFileSync("git", ["-C", root, "config", "user.email", "test@example.test"]);
  execFileSync("git", ["-C", root, "config", "user.name", "Test User"]);
  const notes = buildPrereleaseNotes({ version, channel })
    .replace("待根据本次候选的用户可见变化补充。", "新增测试模式切换。")
    .replace("待根据本次候选的用户可见变化补充。", "改进后台状态提示。")
    .replace("待根据本次候选的用户可见变化补充。", "修复恢复后的状态显示。")
    .replace("待列出本次需要重点覆盖的正常流程、模式切换和故障场景。", "重点覆盖模式切换、后台任务和故障恢复。")
    .replace("请勿在没有备份的日常数据上直接试用。", "请勿在没有备份的日常数据上直接试用。");
  const packageJson = JSON.stringify({ version });
  const lockfile = JSON.stringify({ version, packages: { "": { version } } });
  await Promise.all([
    writeFile(path.join(root, "cli", "pyproject.toml"), `[project]\nversion = "${version}"\n`),
    writeFile(path.join(root, "desktop", "package.json"), packageJson),
    writeFile(path.join(root, "desktop", "package-lock.json"), lockfile),
    writeFile(path.join(root, "frontend", "package.json"), packageJson),
    writeFile(path.join(root, "frontend", "package-lock.json"), lockfile),
    writeFile(path.join(root, "docs", "releases", `v${version}.md`), notes),
    writeFile(path.join(root, "desktop", "release-notes.md"), notes),
    writeFile(
      path.join(root, "desktop", "electron-builder.yml"),
      validBuilderConfig,
    ),
  ]);
  execFileSync("git", ["-C", root, "add", "."]);
  execFileSync("git", ["-C", root, "commit", "-m", "fixture"]);
  const releaseSha = execFileSync("git", ["-C", root, "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
  return { root, notes, releaseSha };
}

test("accepts one clean prerelease candidate bound to version, branch, SHA, and stable feed", async () => {
  const fixture = await createFixture();
  try {
    const contract = await assertPrereleasePreflight({
      version,
      channel,
      sourceBranch,
      releaseSha: fixture.releaseSha,
      repoRoot: fixture.root,
    });
    assert.equal(contract.defaultBackendMode, "split");
    assert.equal(contract.diagnosticsSchemaVersion, 1);
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("rejects unfinished notes, stale metadata, changed stable feed, and another SHA", async () => {
  const fixture = await createFixture();
  const verify = (overrides = {}) => assertPrereleasePreflight({
    version,
    channel,
    sourceBranch,
    releaseSha: fixture.releaseSha,
    repoRoot: fixture.root,
    ...overrides,
  });
  try {
    const notesPath = path.join(fixture.root, "docs", "releases", `v${version}.md`);
    const desktopNotesPath = path.join(fixture.root, "desktop", "release-notes.md");
    const unfinished = fixture.notes.replace("重点覆盖模式切换、后台任务和故障恢复。", "待列出测试重点。");
    await Promise.all([writeFile(notesPath, unfinished), writeFile(desktopNotesPath, unfinished)]);
    await assert.rejects(verify(), /未完成占位文本/);

    await Promise.all([writeFile(notesPath, fixture.notes), writeFile(desktopNotesPath, fixture.notes)]);
    await writeFile(path.join(fixture.root, "frontend", "package.json"), JSON.stringify({ version: "9.9.8" }));
    await assert.rejects(verify(), /frontend\/package\.json 版本为 9\.9\.8/);

    await writeFile(path.join(fixture.root, "frontend", "package.json"), JSON.stringify({ version }));
    await writeFile(path.join(fixture.root, "desktop", "electron-builder.yml"), "publish:\n  releaseType: prerelease\n");
    await assert.rejects(verify(), /稳定版更新入口合同已改变/);

    const changedBuilder = await readFile(path.join(fixture.root, "desktop", "electron-builder.yml"), "utf8");
    assert.match(changedBuilder, /prerelease/);
    await writeFile(path.join(fixture.root, "desktop", "electron-builder.yml"), validBuilderConfig);
    await assert.rejects(verify({ releaseSha: "b".repeat(40) }), /当前提交 .*显式 release_sha/);
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});
