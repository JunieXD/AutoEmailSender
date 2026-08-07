import { describe, expect, it } from "vitest";
import {
  DEV_SHUTDOWN_GRACE_MS,
  buildForcedTermination,
} from "../src/main/dev/launcher.js";


describe("desktop development launcher", () => {
  it("forces the whole Electron process tree to stop on Windows", () => {
    expect(buildForcedTermination("win32", 1234)).toEqual({
      command: "taskkill",
      args: ["/pid", "1234", "/t", "/f"],
    });
  });

  it("allows Electron a graceful shutdown window before force killing", () => {
    expect(DEV_SHUTDOWN_GRACE_MS).toBeGreaterThanOrEqual(3_000);
    expect(buildForcedTermination("darwin", 1234)).toBeNull();
    expect(buildForcedTermination("linux", 1234)).toBeNull();
  });
});
