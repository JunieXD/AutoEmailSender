import { describe, expect, it } from "vitest";

import {
  PACKAGED_QA_GRACEFUL_QUIT_MESSAGE,
  shouldRegisterPackagedQaGracefulQuit,
} from "../src/main/packaged-qa/graceful-quit.js";

describe("packaged QA graceful quit control", () => {
  it("uses the Win32 WM_APP range", () => {
    expect(PACKAGED_QA_GRACEFUL_QUIT_MESSAGE).toBe(0x84a5);
  });

  it("registers only for an authorized Windows packaged QA session", () => {
    expect(shouldRegisterPackagedQaGracefulQuit({
      platform: "win32",
      activeUserDataPath: "C:\\qa\\user-data",
    })).toBe(true);
    expect(shouldRegisterPackagedQaGracefulQuit({
      platform: "win32",
      activeUserDataPath: null,
    })).toBe(false);
    expect(shouldRegisterPackagedQaGracefulQuit({
      platform: "darwin",
      activeUserDataPath: "/qa/user-data",
    })).toBe(false);
  });
});
