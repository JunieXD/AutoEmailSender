export const APP_SCROLL_CONTAINER_SELECTOR = '[data-app-scroll-container="true"]';

export const getAppScrollContainer = (): HTMLElement | null => {
  if (typeof document === "undefined") {
    return null;
  }
  return document.querySelector<HTMLElement>(APP_SCROLL_CONTAINER_SELECTOR);
};

type AppScrollBehavior = ScrollBehavior;

type ScrollElementIntoAppViewOptions = {
  behavior?: AppScrollBehavior;
  offset?: number;
};

const VERTICAL_SCROLL_OVERFLOW_VALUES = new Set(["auto", "overlay", "scroll"]);

const getScrollContainerForElement = (
  element: HTMLElement,
  appContainer: HTMLElement,
) => {
  if (!appContainer.contains(element)) {
    return null;
  }

  let ancestor = element.parentElement;
  while (ancestor && ancestor !== appContainer) {
    const overflowY = window.getComputedStyle(ancestor).overflowY;
    if (VERTICAL_SCROLL_OVERFLOW_VALUES.has(overflowY)) {
      return ancestor;
    }
    ancestor = ancestor.parentElement;
  }

  return appContainer;
};

export const scrollElementIntoAppView = (
  element: HTMLElement,
  { behavior = "auto", offset = 24 }: ScrollElementIntoAppViewOptions = {},
) => {
  const appContainer = getAppScrollContainer();
  const container = appContainer
    ? getScrollContainerForElement(element, appContainer)
    : null;
  if (!container) {
    element.scrollIntoView?.({ behavior, block: "start" });
    return;
  }

  const containerRect = container.getBoundingClientRect();
  const elementRect = element.getBoundingClientRect();
  const top = Math.max(
    0,
    container.scrollTop + elementRect.top - containerRect.top - offset,
  );
  container.scrollTo({ left: container.scrollLeft, top, behavior });
};
