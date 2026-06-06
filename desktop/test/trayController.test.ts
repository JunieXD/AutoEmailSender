import { describe, expect, it, vi } from "vitest";
import { bindTrayInteractions } from "../src/trayController.js";

describe("desktop tray controller", () => {
  it("opens the main window on left click and double click", () => {
    const handlers = new Map<string, () => void>();
    const tray = {
      on: vi.fn((eventName: string, handler: () => void) => {
        handlers.set(eventName, handler);
      }),
    };
    const openWindow = vi.fn();

    bindTrayInteractions(tray, { openWindow });
    handlers.get("click")?.();
    handlers.get("double-click")?.();

    expect(openWindow).toHaveBeenCalledTimes(2);
    expect(tray.on).toHaveBeenCalledWith("click", expect.any(Function));
    expect(tray.on).toHaveBeenCalledWith("double-click", expect.any(Function));
    expect(tray.on).not.toHaveBeenCalledWith("right-click", expect.any(Function));
  });
});
