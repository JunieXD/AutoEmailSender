import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const scriptsRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const stableWrappers = new Map([
  ["build-backend.ps1", "build\\build-backend.ps1"],
  ["build-backend.sh", "build/build-backend.sh"],
  ["build-cli.ps1", "build\\build-cli.ps1"],
  ["build-cli.sh", "build/build-cli.sh"],
  ["install-backend-playwright.ps1", "build\\install-backend-playwright.ps1"],
  ["prepare-release.ps1", "release\\prepare-release.ps1"],
  ["prepare-release.sh", "release/prepare-release.sh"],
  ["release.ps1", "release\\release.ps1"],
  ["release.sh", "release/release.sh"],
  ["verify-release.ps1", "release\\verify-release.ps1"],
  ["verify-release.sh", "release/verify-release.mjs"],
]);

test("script root contains only documented stable wrappers", async () => {
  const entries = await readdir(scriptsRoot, { withFileTypes: true });
  const rootScripts = entries
    .filter((entry) => entry.isFile() && /\.(?:mjs|ps1|py|sh)$/.test(entry.name))
    .map((entry) => entry.name)
    .sort();

  assert.deepEqual(rootScripts, [...stableWrappers.keys()].sort());
});

test("stable wrappers only forward arguments to their owner", async () => {
  for (const [wrapper, ownerPath] of stableWrappers) {
    const source = await readFile(path.join(scriptsRoot, wrapper), "utf8");
    const codeLines = source
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);

    assert.match(source, new RegExp(ownerPath.replaceAll("\\", "\\\\")));
    assert.ok(codeLines.length <= 6, `${wrapper} must remain a thin wrapper`);
  }
});

test("implementation directories remain capability-owned", async () => {
  const expectedOwners = ["build", "data", "packaging", "quality", "release"];
  for (const owner of expectedOwners) {
    const entries = await readdir(path.join(scriptsRoot, owner));
    assert.ok(entries.length > 0, `${owner} must contain its implementation`);
  }
});
