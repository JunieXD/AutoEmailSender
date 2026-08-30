import assert from "node:assert/strict";
import test from "node:test";
import { validateQualityEvidence } from "./quality-evidence.mjs";

const now = Date.parse("2026-08-30T12:00:00Z");
const gitSha = "a".repeat(40);
const toolchain = {
  node: "v24.0.0",
  npm: "11.0.0",
  python: "Python 3.12.0",
  uv: "uv 0.8.0",
};

function evidence(overrides = {}) {
  return {
    schemaVersion: 1,
    kind: "auto-email-sender-quality-evidence",
    gitSha,
    generatedAt: "2026-08-30T11:00:00Z",
    toolchain,
    passedSuites: ["frontend", "backend", "frontend"],
    ...overrides,
  };
}

test("accepts recent evidence bound to the same SHA and toolchain", () => {
  assert.deepEqual(
    validateQualityEvidence(evidence(), { gitSha, toolchain, now }),
    ["backend", "frontend"],
  );
});

test("rejects stale, cross-SHA, and cross-toolchain evidence", () => {
  assert.throws(
    () => validateQualityEvidence(evidence({ gitSha: "b".repeat(40) }), { gitSha, toolchain, now }),
    /当前 SHA/,
  );
  assert.throws(
    () => validateQualityEvidence(evidence({ generatedAt: "2026-08-28T11:00:00Z" }), { gitSha, toolchain, now }),
    /已过期/,
  );
  assert.throws(
    () => validateQualityEvidence(evidence(), { gitSha, toolchain: { ...toolchain, node: "v25.0.0" }, now }),
    /node 工具链/,
  );
});
