import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useDocumentScrollLock } from "./useDocumentScrollLock";

const ScrollLock = ({ locked }: { locked: boolean }) => {
  useDocumentScrollLock(locked);
  return null;
};

afterEach(() => {
  cleanup();
  document.querySelector('[data-app-scroll-container="true"]')?.remove();
  document.body.style.overflow = "";
  document.documentElement.style.overflow = "";
});

describe("useDocumentScrollLock", () => {
  it("keeps the document locked until the final nested lock is released", () => {
    const scrollContainer = document.createElement("div");
    scrollContainer.dataset.appScrollContainer = "true";
    scrollContainer.style.overflowY = "auto";
    document.body.append(scrollContainer);
    document.body.style.overflow = "auto";
    document.documentElement.style.overflow = "scroll";

    const { rerender, unmount } = render(
      <>
        <ScrollLock locked />
        <ScrollLock locked />
      </>,
    );

    expect(document.body.style.overflow).toBe("hidden");
    expect(document.documentElement.style.overflow).toBe("hidden");
    expect(scrollContainer.style.overflowY).toBe("hidden");

    rerender(
      <>
        <ScrollLock locked={false} />
        <ScrollLock locked />
      </>,
    );

    expect(document.body.style.overflow).toBe("hidden");
    expect(document.documentElement.style.overflow).toBe("hidden");
    expect(scrollContainer.style.overflowY).toBe("hidden");

    unmount();

    expect(document.body.style.overflow).toBe("auto");
    expect(document.documentElement.style.overflow).toBe("scroll");
    expect(scrollContainer.style.overflowY).toBe("auto");
  });
});
