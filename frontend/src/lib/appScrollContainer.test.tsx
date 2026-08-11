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

  it("scrolls the nearest nested panel without moving the page behind it", () => {
    const appContainer = document.createElement("div");
    appContainer.dataset.appScrollContainer = "true";
    Object.defineProperties(appContainer, {
      scrollTop: { configurable: true, value: 900, writable: true },
      scrollLeft: { configurable: true, value: 0, writable: true },
    });
    appContainer.getBoundingClientRect = () => ({
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
    const appScrollTo = vi.fn();
    appContainer.scrollTo = appScrollTo;
    document.body.append(appContainer);

    const drawerScroller = document.createElement("div");
    drawerScroller.style.overflowY = "auto";
    Object.defineProperties(drawerScroller, {
      scrollTop: { configurable: true, value: 300, writable: true },
      scrollLeft: { configurable: true, value: 0, writable: true },
    });
    drawerScroller.getBoundingClientRect = () => ({
      top: 180,
      left: 200,
      right: 900,
      bottom: 760,
      width: 700,
      height: 580,
      x: 200,
      y: 180,
      toJSON: () => ({}),
    });
    const drawerScrollTo = vi.fn();
    drawerScroller.scrollTo = drawerScrollTo;
    appContainer.append(drawerScroller);

    const target = document.createElement("section");
    target.getBoundingClientRect = () => ({
      top: 540,
      left: 220,
      right: 880,
      bottom: 620,
      width: 660,
      height: 80,
      x: 220,
      y: 540,
      toJSON: () => ({}),
    });
    drawerScroller.append(target);

    scrollElementIntoAppView(target, { offset: 24 });

    expect(drawerScrollTo).toHaveBeenCalledWith({
      left: 0,
      top: 636,
      behavior: "auto",
    });
    expect(appScrollTo).not.toHaveBeenCalled();
    expect(appContainer.scrollTop).toBe(900);
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
