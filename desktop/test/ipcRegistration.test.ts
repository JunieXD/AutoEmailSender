import { describe, expect, it } from "vitest";

import {
  isAgentIntegrationId,
  isAgentSupportEnableOptions,
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
});
