import assert from "node:assert/strict";
import test from "node:test";

import { planReleaseImpact } from "./release-impact.mjs";

function requiredIds(plan) {
  return plan.required.map(({ id }) => id);
}

test("release-note-only changes skip both expensive platform checks", () => {
  const plan = planReleaseImpact([
    "docs/releases/v2.5.5.md",
    "desktop/release-notes.md",
  ]);

  assert.deepEqual(plan.categories, ["release-note-only"]);
  assert.deepEqual(requiredIds(plan), ["release-metadata"]);
  assert.deepEqual(
    plan.expensiveChecksSkipped.map(({ id }) => id),
    ["windows-formal-qa", "macos-sparkle-candidate"],
  );
});

test("CLI test fixture changes do not rebuild frozen products", () => {
  const plan = planReleaseImpact(["cli/test/test_version.py"]);

  assert.deepEqual(plan.categories, ["cli-tests"]);
  assert.deepEqual(requiredIds(plan), ["cli-suite"]);
});

test("CLI product changes require frozen and quick Windows validation", () => {
  const plan = planReleaseImpact(["cli/auto_email_sender/main.py"]);

  assert.deepEqual(plan.categories, ["cli-product"]);
  assert.deepEqual(requiredIds(plan), ["cli-suite", "cli-frozen-build", "windows-quick-qa"]);
});

test("release orchestration changes run contracts without rebuilding installers", () => {
  const plan = planReleaseImpact([
    ".github/workflows/release.yml",
    ".codex/skills/auto-email-sender-release/SKILL.md",
  ]);

  assert.deepEqual(plan.categories, ["release-orchestration"]);
  assert.deepEqual(requiredIds(plan), ["release-contracts", "windows-release-contracts"]);
});

test("Windows QA runner changes exercise quick mode without rebuilding NSIS", () => {
  const plan = planReleaseImpact(["scripts/quality/run-windows-release-qa.ps1"]);

  assert.deepEqual(plan.categories, ["release-orchestration"]);
  assert.deepEqual(requiredIds(plan), [
    "release-contracts",
    "windows-release-contracts",
    "windows-quick-qa",
  ]);
});

test("Windows packaging changes replace quick QA with formal QA", () => {
  const plan = planReleaseImpact(["desktop/electron-builder.yml"]);

  assert.deepEqual(plan.categories, ["desktop", "windows-packaging"]);
  assert.deepEqual(requiredIds(plan), ["desktop-suite", "windows-formal-qa"]);
});

test("Sparkle helper changes require release contracts and macOS certification", () => {
  const plan = planReleaseImpact(["scripts/release/prepare-sparkle-release.mjs"]);

  assert.deepEqual(plan.categories, ["macos-sparkle-packaging", "release-orchestration"]);
  assert.deepEqual(requiredIds(plan), [
    "release-contracts",
    "windows-release-contracts",
    "macos-sparkle-candidate",
  ]);
});

test("Sparkle-native desktop changes do not trigger Windows QA", () => {
  const plan = planReleaseImpact(["desktop/native/sparkle/SparkleController.mm"]);

  assert.deepEqual(plan.categories, ["desktop", "macos-sparkle-packaging"]);
  assert.deepEqual(requiredIds(plan), ["desktop-suite", "macos-sparkle-candidate"]);
});

test("a frozen candidate always receives one formal dual-platform certification", () => {
  const plan = planReleaseImpact(["docs/releases/v2.5.5.md"], { candidate: true });

  assert.deepEqual(plan.categories, ["frozen-release-candidate", "release-note-only"]);
  assert.deepEqual(requiredIds(plan), [
    "release-metadata",
    "release-contracts",
    "windows-release-contracts",
    "windows-formal-qa",
    "macos-sparkle-candidate",
  ]);
  assert.deepEqual(plan.expensiveChecksSkipped, []);
});
