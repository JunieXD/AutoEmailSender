import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getAppScrollContainer,
  scrollElementIntoAppView,
} from "./appScrollContainer";

afterEach(() => {
  document.querySelector('[data-app-scroll-container="true"]')?.remove();
});

describe("appScrollContainer", () => {
  it("positions a target inside the dedicated application scroller", () => {
    const container = document.createElement("div");
    container.dataset.appScrollContainer = "true";
    Object.defineProperty(container, "scrollTop", {
      configurable: true,
      value: 200,
      writable: true,
    });
    Object.defineProperty(container, "scrollLeft", {
      configurable: true,
      value: 0,
      writable: true,
    });
    container.getBoundingClientRect = () => ({
      top: 120,
      left: 0,
      right: 1000,
      bottom: 800,
      width: 1000,
      height: 680,
      x: 0,
      y: 120,
      toJSON: () => ({}),
    });
    const scrollTo = vi.fn();
    container.scrollTo = scrollTo;
    document.body.append(container);

    const target = document.createElement("section");
    target.getBoundingClientRect = () => ({
      top: 620,
      left: 0,
      right: 800,
      bottom: 720,
      width: 800,
      height: 100,
      x: 0,
      y: 620,
      toJSON: () => ({}),
    });
    container.append(target);

    scrollElementIntoAppView(target, { behavior: "smooth", offset: 24 });

    expect(getAppScrollContainer()).toBe(container);
    expect(scrollTo).toHaveBeenCalledWith({
      left: 0,
      top: 676,
      behavior: "smooth",
    });
  });

  it("falls back to native scrollIntoView outside the application shell", () => {
    const target = document.createElement("section");
    const scrollIntoView = vi.fn();
    target.scrollIntoView = scrollIntoView;

    scrollElementIntoAppView(target);

    expect(scrollIntoView).toHaveBeenCalledWith({
      behavior: "auto",
      block: "start",
    });
  });
});
