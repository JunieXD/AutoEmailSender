import { describe, expect, it } from "vitest";

import {
  BETA_DIAGNOSTIC_FREE_TEXT_OMITTED,
  BETA_DIAGNOSTIC_REDACTED,
  sanitizeDiagnosticFreeText,
  sanitizeDiagnosticLogText,
  sanitizeTimelineDetails,
} from "../src/main/diagnostics/redaction.js";

describe("beta diagnostics redaction", () => {
  it("removes credentials, personal identifiers, paths, remote URLs and IPs", () => {
    const canaries = {
      token: "CANARY-token-77",
      password: "CANARY-password-88",
      email: "canary.student@example.test",
      person: "张三",
      home: "/Users/canary/Documents/private.txt",
      windowsHome: "C:\\Users\\Canary\\Documents\\private.txt",
      remoteUrl: "https://faculty.example.test/profile?token=secret#bio",
      remoteIp: "203.0.113.42",
      machine: "canary-macbook",
    };
    const sanitized = sanitizeDiagnosticLogText(
      [
        `token=${canaries.token}`,
        `password=${canaries.password}`,
        `email=${canaries.email}`,
        `姓名：${canaries.person}`,
        canaries.home,
        canaries.windowsHome,
        canaries.remoteUrl,
        canaries.remoteIp,
        canaries.machine,
        "http://127.0.0.1:48120/api/runtime?secret=value#fragment",
      ].join("\n"),
      {
        homePath: "/Users/canary",
        userDataPath: "/Users/canary/Library/Application Support/Auto Email Sender",
        machineName: canaries.machine,
      },
    );

    for (const canary of Object.values(canaries)) {
      expect(sanitized).not.toContain(canary);
    }
    expect(sanitized).toContain(BETA_DIAGNOSTIC_FREE_TEXT_OMITTED);
    expect(sanitized).toContain("email_delivery");
    expect(sanitized).toContain("network");
    expect(sanitized).not.toContain("http://127.0.0.1:48120/api/runtime");
  });

  it("uses a strict allowlist for structured timeline details", () => {
    expect(sanitizeTimelineDetails({
      state: "ready",
      api_pid: 42,
      api_available: true,
      email: "private@example.test",
      professor_name: "Private Person",
      body: "private body",
      note: "token=secret name=Alice email=alice@example.test",
      reason: "https://private.example.test/profile",
      source: "203.0.113.42",
      phase: "canary-macbook",
      nested: { password: "secret" },
    }, { machineName: "canary-macbook" })).toEqual({
      state: "ready",
      api_pid: 42,
      api_available: true,
      note: "[FREE_TEXT_OMITTED tags=email_delivery]",
      reason: BETA_DIAGNOSTIC_REDACTED,
      source: BETA_DIAGNOSTIC_REDACTED,
      phase: BETA_DIAGNOSTIC_REDACTED,
    });
  });

  it("omits arbitrary free text, including unlabelled names and control characters", () => {
    const sanitized = sanitizeDiagnosticFreeText(`hello\u0000${"x".repeat(500)}`);
    expect(sanitized).not.toContain("\u0000");
    expect(sanitized).toBe(BETA_DIAGNOSTIC_FREE_TEXT_OMITTED);
    expect(sanitizeDiagnosticFreeText("刚才张三的邮件发送后卡住了")).toBe(
      "[FREE_TEXT_OMITTED tags=background_stall,email_delivery]",
    );
  });

  it("keeps only its own already-sanitized diagnostic tag representation", () => {
    const sanitized = "[FREE_TEXT_OMITTED tags=database,timeout]";
    expect(sanitizeDiagnosticFreeText(sanitized)).toBe(sanitized);
    expect(sanitizeDiagnosticFreeText("[FREE_TEXT_OMITTED tags=alice_smith]")).toBe(
      BETA_DIAGNOSTIC_FREE_TEXT_OMITTED,
    );
  });
});
