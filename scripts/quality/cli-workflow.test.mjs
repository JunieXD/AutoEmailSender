import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const workflowsRoot = path.join(repositoryRoot, ".github", "workflows");
const [contractWorkflow, binaryWorkflow, releaseWorkflow] = await Promise.all([
  readFile(path.join(workflowsRoot, "cli.yml"), "utf8"),
  readFile(path.join(workflowsRoot, "cli-binaries.yml"), "utf8"),
  readFile(path.join(workflowsRoot, "release.yml"), "utf8"),
]);

test("ordinary CLI pushes run contracts on Ubuntu only", () => {
  assert.match(contractWorkflow, /ubuntu-contract-tests:[\s\S]*runs-on: ubuntu-latest/);
  assert.match(
    contractWorkflow,
    /cross-platform-contract-tests:\n    if: github\.event_name == 'pull_request' \|\| github\.event_name == 'workflow_dispatch'/,
  );
  assert.doesNotMatch(contractWorkflow, /Build and verify frozen CLI/);
});

test("skill-only and test-only changes do not build frozen binaries", () => {
  assert.match(contractWorkflow, /agent-support\/skills\/auto-email-sender\/\*\*/);
  assert.match(contractWorkflow, /- "cli\/\*\*"/);
  assert.match(binaryWorkflow, /- "cli\/src\/\*\*"/);
  assert.doesNotMatch(binaryWorkflow, /agent-support\/skills/);
  assert.doesNotMatch(binaryWorkflow, /cli\/test/);
  assert.doesNotMatch(binaryWorkflow, /- "cli\/\*\*"/);
});

test("frozen binaries build only on supported desktop platforms", () => {
  assert.match(binaryWorkflow, /- macos-latest\n          - windows-latest/);
  assert.doesNotMatch(binaryWorkflow, /ubuntu-latest/);
  assert.doesNotMatch(binaryWorkflow, /unittest discover/);
  assert.match(binaryWorkflow, /\.\/scripts\/build\/build-cli\.sh --clean/);
  assert.match(binaryWorkflow, /\.\/scripts\/build\/build-cli\.ps1 -Clean/);
});

test("release tags retain three-platform contract coverage", () => {
  assert.match(contractWorkflow, /tags:\n      - "v\*"/);
  assert.equal(
    releaseWorkflow.match(/- name: Test Agent CLI/g)?.length,
    2,
    "release workflow must test the CLI on Windows and macOS",
  );
});
