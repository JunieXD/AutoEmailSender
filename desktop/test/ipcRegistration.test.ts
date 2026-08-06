import { describe, expect, it } from "vitest";

import { isAgentIntegrationId } from "../src/main/ipc/register.js";


describe("desktop IPC registration", () => {
  it("accepts only supported Agent integration identifiers", () => {
    expect(isAgentIntegrationId("codex")).toBe(true);
    expect(isAgentIntegrationId("claude_code")).toBe(true);
    expect(isAgentIntegrationId("cursor")).toBe(true);
    expect(isAgentIntegrationId("copilot_cli")).toBe(true);
    expect(isAgentIntegrationId("unknown")).toBe(false);
    expect(isAgentIntegrationId(null)).toBe(false);
  });
});
