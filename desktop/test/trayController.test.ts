import { describe, expect, it, vi } from "vitest";
import { bindTrayInteractions } from "../src/trayController.js";

describe("desktop tray controller", () => {
  it("opens the main window on left click and double click", () => {
    const events: string[] = [];
    const handlers = new Map<string, () => void>();
    const tray = {
      on: vi.fn((eventName: string, handler: () => void) => {
        handlers.set(eventName, handler);
      }),
      popUpContextMenu: vi.fn(),
    };
    const openWindow = vi.fn();
    const buildContextMenu = vi.fn(() => ({ id: "menu" }));

    bindTrayInteractions(tray, { openWindow, buildContextMenu, logEvent: (eventName) => events.push(eventName) });
    handlers.get("click")?.();
    handlers.get("double-click")?.();

    expect(openWindow).toHaveBeenCalledTimes(2);
    expect(tray.on).toHaveBeenCalledWith("click", expect.any(Function));
    expect(tray.on).toHaveBeenCalledWith("double-click", expect.any(Function));
    expect(events).toEqual(["tray.click", "tray.double-click"]);
  });

  it("explicitly opens the context menu on right click", () => {
    const events: string[] = [];
    const handlers = new Map<string, () => void>();
    const tray = {
      on: vi.fn((eventName: string, handler: () => void) => {
        handlers.set(eventName, handler);
      }),
      popUpContextMenu: vi.fn(),
    };
    const menu = { id: "menu" };

    bindTrayInteractions(tray, {
      openWindow: vi.fn(),
      buildContextMenu: () => menu,
      logEvent: (eventName) => events.push(eventName),
    });
    handlers.get("right-click")?.();

    expect(tray.popUpContextMenu).toHaveBeenCalledWith(menu);
    expect(events).toEqual(["tray.right-click"]);
  });
});
