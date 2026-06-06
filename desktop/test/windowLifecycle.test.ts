import { describe, expect, it } from "vitest";
import {
  restoreExistingWindow,
  shouldHideWindowOnClose,
  startWindowCreationOnce,
} from "../src/windowLifecycle.js";

describe("desktop window lifecycle", () => {
  it("hides the window instead of quitting on normal close", () => {
    expect(
      shouldHideWindowOnClose({
        isPackaged: true,
        isQuitting: false,
        platform: "win32",
      }),
    ).toBe(true);
  });

  it("allows the app to quit after an explicit exit action", () => {
    expect(
      shouldHideWindowOnClose({
        isPackaged: true,
        isQuitting: true,
        platform: "win32",
      }),
    ).toBe(false);
  });

  it("allows Linux dev windows to close instead of hiding to an inaccessible tray", () => {
    expect(
      shouldHideWindowOnClose({
        isPackaged: false,
        isQuitting: false,
        platform: "linux",
      }),
    ).toBe(false);
  });

  it("restores, shows, and focuses an existing window", () => {
    const calls: string[] = [];
    const window = {
      isMinimized: () => true,
      restore: () => calls.push("restore"),
      show: () => calls.push("show"),
      focus: () => calls.push("focus"),
    };

    restoreExistingWindow(window);

    expect(calls).toEqual(["restore", "show", "focus"]);
  });

  it("does not restore a window that is not minimized", () => {
    const calls: string[] = [];
    const window = {
      isMinimized: () => false,
      restore: () => calls.push("restore"),
      show: () => calls.push("show"),
      focus: () => calls.push("focus"),
    };

    restoreExistingWindow(window);

    expect(calls).toEqual(["show", "focus"]);
  });

  it("reuses an in-flight window creation", async () => {
    const state = { pendingCreation: null };
    let createCalls = 0;
    let finishCreation: (() => void) | undefined;

    const first = startWindowCreationOnce(state, () => {
      createCalls += 1;
      return new Promise<void>((resolve) => {
        finishCreation = resolve;
      });
    });
    const second = startWindowCreationOnce(state, () => {
      createCalls += 1;
      return Promise.resolve();
    });

    expect(second).toBe(first);
    expect(createCalls).toBe(1);

    finishCreation?.();
    await first;

    expect(state.pendingCreation).toBeNull();
  });
});
