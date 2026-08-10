import { describe, expect, it } from "vitest";

import {
  isAgentIntegrationId,
  isAgentSupportEnableOptions,
  isBetaDiagnosticsProblemInput,
  isBetaDiagnosticsRange,
} from "../src/main/ipc/register.js";


describe("desktop IPC registration", () => {
  it("accepts only supported Agent integration identifiers", () => {
    expect(isAgentIntegrationId("codex")).toBe(true);
    expect(isAgentIntegrationId("claude_code")).toBe(true);
    expect(isAgentIntegrationId("cursor")).toBe(true);
    expect(isAgentIntegrationId("copilot_cli")).toBe(true);
    expect(isAgentIntegrationId("unknown")).toBe(false);
    expect(isAgentIntegrationId(null)).toBe(false);
  });

  it("accepts only the supported Agent enable option", () => {
    expect(isAgentSupportEnableOptions(undefined)).toBe(true);
    expect(isAgentSupportEnableOptions({})).toBe(true);
    expect(isAgentSupportEnableOptions({ installDetectedAgents: true })).toBe(true);
    expect(isAgentSupportEnableOptions({ installDetectedAgents: false })).toBe(true);
    expect(isAgentSupportEnableOptions({ installDetectedAgents: "yes" })).toBe(false);
    expect(isAgentSupportEnableOptions({ unknown: true })).toBe(false);
    expect(isAgentSupportEnableOptions(null)).toBe(false);
  });

  it("accepts only bounded Beta diagnostic ranges and problem markers", () => {
    expect(isBetaDiagnosticsRange("1h")).toBe(true);
    expect(isBetaDiagnosticsRange("24h")).toBe(true);
    expect(isBetaDiagnosticsRange("7d")).toBe(true);
    expect(isBetaDiagnosticsRange("all")).toBe(true);
    expect(isBetaDiagnosticsRange("30d")).toBe(false);
    expect(isBetaDiagnosticsProblemInput({ category: "database" })).toBe(true);
    expect(isBetaDiagnosticsProblemInput({
      category: "background_stall",
      note: "刚才后台任务没有继续运行",
    })).toBe(true);
    expect(isBetaDiagnosticsProblemInput({ category: "unknown" })).toBe(false);
    expect(isBetaDiagnosticsProblemInput({
      category: "general",
      note: "x".repeat(241),
    })).toBe(false);
    expect(isBetaDiagnosticsProblemInput({
      category: "general",
      note: "bad\u0000note",
    })).toBe(false);
    expect(isBetaDiagnosticsProblemInput({ category: "general", extra: true })).toBe(false);
  });
});
