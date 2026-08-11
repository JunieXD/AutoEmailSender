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

export const scrollElementIntoAppView = (
  element: HTMLElement,
  { behavior = "auto", offset = 24 }: ScrollElementIntoAppViewOptions = {},
) => {
  const container = getAppScrollContainer();
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
