import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { assertReleasePreflight } from "./release-preflight.mjs";

const version = "9.9.9";
const tag = `v${version}`;
const note = `# ${tag}\n\n### 新增功能\n\n### 体验优化\n\n### 问题修复\n\n## 安装说明\n\n## 自动更新\n\n## 导师抓取 Skill\n`;

async function createFixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "release-preflight-test-"));
  await Promise.all([
    mkdir(path.join(root, "cli"), { recursive: true }),
    mkdir(path.join(root, "desktop"), { recursive: true }),
    mkdir(path.join(root, "frontend"), { recursive: true }),
    mkdir(path.join(root, "docs", "releases"), { recursive: true }),
  ]);
  const packageJson = JSON.stringify({ version });
  const lockfile = JSON.stringify({ version, packages: { "": { version } } });
  await Promise.all([
    writeFile(path.join(root, "cli", "pyproject.toml"), `[project]\nversion = "${version}"\n`),
    writeFile(path.join(root, "desktop", "package.json"), packageJson),
    writeFile(path.join(root, "desktop", "package-lock.json"), lockfile),
    writeFile(path.join(root, "frontend", "package.json"), packageJson),
    writeFile(path.join(root, "frontend", "package-lock.json"), lockfile),
    writeFile(path.join(root, "docs", "releases", `${tag}.md`), note),
    writeFile(path.join(root, "desktop", "release-notes.md"), note),
  ]);
  return root;
}

test("accepts a consistent frozen release candidate", async () => {
  const root = await createFixture();
  try {
    assert.deepEqual(await assertReleasePreflight({ version, repoRoot: root }), { version, tag });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("rejects stale package versions and release note copies", async () => {
  const root = await createFixture();
  try {
    await writeFile(path.join(root, "frontend", "package.json"), JSON.stringify({ version: "9.9.8" }));
    await assert.rejects(
      assertReleasePreflight({ version, repoRoot: root }),
      /frontend\/package\.json 版本为 9\.9\.8/,
    );
    await writeFile(path.join(root, "frontend", "package.json"), JSON.stringify({ version }));
    await writeFile(path.join(root, "desktop", "release-notes.md"), `${note}\nchanged\n`);
    await assert.rejects(
      assertReleasePreflight({ version, repoRoot: root }),
      /发布公告不一致/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
