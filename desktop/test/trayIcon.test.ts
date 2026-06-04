import { describe, expect, it, vi } from "vitest";
import { createTrayIcon } from "../src/trayIcon.js";

describe("desktop tray icon", () => {
  it("resizes a loaded native image for the Windows tray", () => {
    const resized = { isEmpty: () => false, getSize: () => ({ width: 16, height: 16 }), resize: vi.fn() };
    const image = {
      isEmpty: () => false,
      getSize: () => ({ width: 256, height: 256 }),
      resize: vi.fn(() => resized),
    };
    const nativeImage = { createFromPath: vi.fn(() => image) };

    const result = createTrayIcon({
      iconPath: "C:\App\resources\build\icon.ico",
      nativeImage,
    });

    expect(nativeImage.createFromPath).toHaveBeenCalledWith("C:\App\resources\build\icon.ico");
    expect(image.resize).toHaveBeenCalledWith({ width: 16, height: 16 });
    expect(result.image).toBe(resized);
    expect(result.description).toContain("empty=false");
    expect(result.description).toContain("size=16x16");
  });

  it("keeps the original image when loading returns an empty image", () => {
    const image = {
      isEmpty: () => true,
      getSize: () => ({ width: 0, height: 0 }),
      resize: vi.fn(),
    };
    const nativeImage = { createFromPath: vi.fn(() => image) };

    const result = createTrayIcon({
      iconPath: "C:\App\resources\build\icon.ico",
      nativeImage,
    });

    expect(image.resize).not.toHaveBeenCalled();
    expect(result.image).toBe(image);
    expect(result.description).toContain("empty=true");
  });
});
