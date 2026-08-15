import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
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
const allWorkflows = (
  await Promise.all(
    (await readdir(workflowsRoot))
      .filter((name) => /\.ya?ml$/.test(name))
      .map((name) => readFile(path.join(workflowsRoot, name), "utf8")),
  )
).join("\n");

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

test("CLI benchmark changes trigger contracts and both frozen builds", () => {
  const benchmarkPath = '- "scripts/quality/benchmark_agent_cli.py"';
  for (const workflow of [contractWorkflow, binaryWorkflow]) {
    assert.equal(
      workflow.split(benchmarkPath).length - 1,
      2,
      "benchmark changes must trigger both push and pull_request validation",
    );
  }
});

test("Agent manifest contract changes trigger contracts and both frozen builds", () => {
  const manifestContractPath = '- "contracts/agent-support-manifest.schema.json"';
  for (const workflow of [contractWorkflow, binaryWorkflow]) {
    assert.equal(
      workflow.split(manifestContractPath).length - 1,
      2,
      "manifest contract changes must trigger push and pull_request validation",
    );
  }
});

test("workflows use Node.js 24 compatible official action generations", () => {
  const staleActions = [
    /actions\/checkout@v[1-6]\b/,
    /actions\/setup-python@v[1-6]\b/,
    /actions\/setup-node@v[1-6]\b/,
    /astral-sh\/setup-uv@v[1-8]\b/,
    /actions\/upload-artifact@v[1-6]\b/,
    /actions\/download-artifact@v[1-7]\b/,
  ];
  for (const staleAction of staleActions) {
    assert.doesNotMatch(allWorkflows, staleAction);
  }
});

test("release tags gate platform builds on one Ubuntu CLI contract suite", () => {
  assert.match(contractWorkflow, /tags:\n      - "v\*"/);
  assert.equal(
    releaseWorkflow.match(/- name: Test Agent CLI/g)?.length,
    1,
    "release workflow must run the platform-independent CLI suite once",
  );
  assert.equal(releaseWorkflow.match(/- name: Install Agent CLI dependencies/g)?.length, 2);
  assert.match(releaseWorkflow, /build-windows:[\s\S]*needs: preflight/);
  assert.match(releaseWorkflow, /build-macos:[\s\S]*needs: preflight/);
  assert.match(releaseWorkflow, /\.\/scripts\/build\/build-cli\.ps1 -Clean -SkipSync/);
  assert.match(releaseWorkflow, /\.\/scripts\/build\/build-cli\.sh --clean/);
});
