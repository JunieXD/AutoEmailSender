import { describe, expect, it, vi } from "vitest";
import {
  isProtectedBackendNavigation,
  preventProtectedBackendNavigation,
} from "../src/main/shell/backend-navigation-guard.js";

describe("backend navigation guard", () => {
  it.each([
    "http://127.0.0.1:48123/api",
    "http://127.0.0.1:48123/api/professors/export?format=xlsx",
    "http://127.0.0.1:48123/api/materials/17/download",
  ])("identifies protected backend navigation: %s", (targetUrl) => {
    expect(
      isProtectedBackendNavigation(targetUrl, "http://127.0.0.1:48123"),
    ).toBe(true);
  });

  it.each([
    "http://127.0.0.1:48123/health",
    "http://127.0.0.1:48123/apiary",
    "http://127.0.0.1:5173/api/professors/export",
    "https://example.edu/api/profile",
    "file:///Applications/AutoEmailSender/index.html",
  ])("allows non-backend navigation: %s", (targetUrl) => {
    expect(
      isProtectedBackendNavigation(targetUrl, "http://127.0.0.1:48123"),
    ).toBe(false);
  });

  it("prevents matching navigation and reports that it handled the event", () => {
    const event = { preventDefault: vi.fn() };

    expect(
      preventProtectedBackendNavigation(
        event,
        "http://127.0.0.1:48123/api/professors/export",
        "http://127.0.0.1:48123",
      ),
    ).toBe(true);
    expect(event.preventDefault).toHaveBeenCalledOnce();
  });
});
