import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";
import { createPortal } from "react-dom";

const TOOLTIP_ID = "truncated-text-tooltip";
const HOVER_DELAY_MS = 1000;
const HIDE_DELAY_MS = 120;
const VIEWPORT_PADDING = 12;
const TOOLTIP_GAP = 8;

type TooltipTarget = {
  element: HTMLElement;
  text: string;
};

type TooltipPosition = {
  left: number;
  top: number;
};

const hasTruncationStyle = (element: HTMLElement): boolean => {
  const hasTruncationClass = Array.from(element.classList).some(
    (className) => className === "truncate" || /^line-clamp-[1-9]\d*$/.test(className),
  );
  if (hasTruncationClass) {
    return true;
  }

  const style = window.getComputedStyle(element);
  const lineClamp = style.getPropertyValue("-webkit-line-clamp");
  return style.textOverflow === "ellipsis" || !["", "0", "none"].includes(lineClamp);
};

const isActuallyTruncated = (element: HTMLElement): boolean =>
  element.scrollWidth > element.clientWidth + 1 ||
  element.scrollHeight > element.clientHeight + 1;

const findTruncationTarget = (eventTarget: EventTarget | null): HTMLElement | null => {
  let element =
    eventTarget instanceof HTMLElement
      ? eventTarget
      : eventTarget instanceof Element
        ? eventTarget.parentElement
        : null;

  while (element && element !== document.body) {
    if (hasTruncationStyle(element)) {
      return element;
    }
    element = element.parentElement;
  }

  return null;
};

const readTooltipText = (element: HTMLElement): string =>
  (
    element.getAttribute("data-overflow-tooltip-content") ??
    element.getAttribute("title") ??
    element.textContent ??
    ""
  ).trim();

const createTooltipTarget = (element: HTMLElement): TooltipTarget | null => {
  const text = readTooltipText(element);
  return text ? { element, text } : null;
};

export const TruncatedTextTooltipProvider = () => {
  const [activeTarget, setActiveTarget] = useState<TooltipTarget | null>(null);
  const [position, setPosition] = useState<TooltipPosition | null>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const hoveredTargetRef = useRef<TooltipTarget | null>(null);
  const focusedTargetRef = useRef<TooltipTarget | null>(null);
  const hoverDelayRef = useRef<number | null>(null);
  const hideDelayRef = useRef<number | null>(null);
  const suppressedTitleRef = useRef<{ element: HTMLElement; title: string } | null>(null);

  const clearHoverDelay = useCallback(() => {
    if (hoverDelayRef.current !== null) {
      window.clearTimeout(hoverDelayRef.current);
      hoverDelayRef.current = null;
    }
  }, []);

  const clearHideDelay = useCallback(() => {
    if (hideDelayRef.current !== null) {
      window.clearTimeout(hideDelayRef.current);
      hideDelayRef.current = null;
    }
  }, []);

  const restoreSuppressedTitle = useCallback(() => {
    const suppressedTitle = suppressedTitleRef.current;
    if (!suppressedTitle) {
      return;
    }
    if (suppressedTitle.element.isConnected && !suppressedTitle.element.hasAttribute("title")) {
      suppressedTitle.element.setAttribute("title", suppressedTitle.title);
    }
    suppressedTitleRef.current = null;
  }, []);

  const suppressNativeTitle = useCallback(
    (element: HTMLElement) => {
      restoreSuppressedTitle();
      const title = element.getAttribute("title");
      if (title === null) {
        return;
      }
      suppressedTitleRef.current = { element, title };
      element.removeAttribute("title");
    },
    [restoreSuppressedTitle],
  );

  const finishHover = useCallback(() => {
    clearHoverDelay();
    clearHideDelay();
    hoveredTargetRef.current = null;
    restoreSuppressedTitle();
    setActiveTarget(focusedTargetRef.current);
  }, [clearHideDelay, clearHoverDelay, restoreSuppressedTitle]);

  const scheduleFinishHover = useCallback(() => {
    clearHideDelay();
    hideDelayRef.current = window.setTimeout(finishHover, HIDE_DELAY_MS);
  }, [clearHideDelay, finishHover]);

  useEffect(() => {
    const handleMouseOver = (event: MouseEvent) => {
      const element = findTruncationTarget(event.target);
      if (!element || hoveredTargetRef.current?.element === element) {
        return;
      }

      clearHoverDelay();
      clearHideDelay();
      restoreSuppressedTitle();

      const target = createTooltipTarget(element);
      if (!target) {
        hoveredTargetRef.current = null;
        return;
      }

      hoveredTargetRef.current = target;
      suppressNativeTitle(element);
      if (!isActuallyTruncated(element)) {
        return;
      }

      hoverDelayRef.current = window.setTimeout(() => {
        hoverDelayRef.current = null;
        if (
          hoveredTargetRef.current?.element === element &&
          element.isConnected &&
          isActuallyTruncated(element) &&
          focusedTargetRef.current === null
        ) {
          setActiveTarget(target);
        }
      }, HOVER_DELAY_MS);
    };

    const handleMouseOut = (event: MouseEvent) => {
      const hoveredTarget = hoveredTargetRef.current;
      if (!hoveredTarget) {
        return;
      }

      const nextTarget = event.relatedTarget;
      if (
        nextTarget instanceof Node &&
        (hoveredTarget.element.contains(nextTarget) || tooltipRef.current?.contains(nextTarget))
      ) {
        return;
      }
      scheduleFinishHover();
    };

    const handleFocusIn = (event: FocusEvent) => {
      const element = findTruncationTarget(event.target);
      if (!element || !isActuallyTruncated(element)) {
        return;
      }
      const target =
        hoveredTargetRef.current?.element === element
          ? hoveredTargetRef.current
          : createTooltipTarget(element);
      if (!target) {
        return;
      }
      focusedTargetRef.current = target;
      setActiveTarget(target);
    };

    const handleFocusOut = (event: FocusEvent) => {
      const focusedTarget = focusedTargetRef.current;
      if (!focusedTarget) {
        return;
      }
      if (
        event.relatedTarget instanceof Node &&
        focusedTarget.element.contains(event.relatedTarget)
      ) {
        return;
      }
      focusedTargetRef.current = null;
      setActiveTarget(null);
    };

    document.addEventListener("mouseover", handleMouseOver);
    document.addEventListener("mouseout", handleMouseOut);
    document.addEventListener("focusin", handleFocusIn);
    document.addEventListener("focusout", handleFocusOut);

    return () => {
      document.removeEventListener("mouseover", handleMouseOver);
      document.removeEventListener("mouseout", handleMouseOut);
      document.removeEventListener("focusin", handleFocusIn);
      document.removeEventListener("focusout", handleFocusOut);
    };
  }, [
    clearHideDelay,
    clearHoverDelay,
    restoreSuppressedTitle,
    scheduleFinishHover,
    suppressNativeTitle,
  ]);

  useEffect(() => {
    if (!activeTarget) {
      return;
    }
    const previousDescription = activeTarget.element.getAttribute("aria-describedby");
    const descriptions = new Set(previousDescription?.split(/\s+/).filter(Boolean));
    descriptions.add(TOOLTIP_ID);
    activeTarget.element.setAttribute("aria-describedby", Array.from(descriptions).join(" "));

    return () => {
      if (previousDescription === null) {
        activeTarget.element.removeAttribute("aria-describedby");
      } else {
        activeTarget.element.setAttribute("aria-describedby", previousDescription);
      }
    };
  }, [activeTarget]);

  useLayoutEffect(() => {
    if (!activeTarget || !tooltipRef.current) {
      setPosition(null);
      return;
    }

    const updatePosition = () => {
      if (!activeTarget.element.isConnected || !tooltipRef.current) {
        setActiveTarget((current) =>
          current?.element === activeTarget.element ? null : current,
        );
        return;
      }

      const targetRect = activeTarget.element.getBoundingClientRect();
      const tooltipRect = tooltipRef.current.getBoundingClientRect();
      const maxLeft = Math.max(
        VIEWPORT_PADDING,
        window.innerWidth - tooltipRect.width - VIEWPORT_PADDING,
      );
      const left = Math.min(
        maxLeft,
        Math.max(
          VIEWPORT_PADDING,
          targetRect.left + targetRect.width / 2 - tooltipRect.width / 2,
        ),
      );
      const fitsAbove = targetRect.top - tooltipRect.height - TOOLTIP_GAP >= VIEWPORT_PADDING;
      const preferredTop = fitsAbove
        ? targetRect.top - tooltipRect.height - TOOLTIP_GAP
        : targetRect.bottom + TOOLTIP_GAP;
      const maxTop = Math.max(
        VIEWPORT_PADDING,
        window.innerHeight - tooltipRect.height - VIEWPORT_PADDING,
      );
      const top = Math.min(maxTop, Math.max(VIEWPORT_PADDING, preferredTop));
      setPosition({ left: Math.round(left), top: Math.round(top) });
    };

    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    const resizeObserver =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(updatePosition);
    resizeObserver?.observe(activeTarget.element);
    resizeObserver?.observe(tooltipRef.current);

    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
      resizeObserver?.disconnect();
    };
  }, [activeTarget]);

  useEffect(
    () => () => {
      clearHoverDelay();
      clearHideDelay();
      restoreSuppressedTitle();
    },
    [clearHideDelay, clearHoverDelay, restoreSuppressedTitle],
  );

  if (!activeTarget || typeof document === "undefined") {
    return null;
  }

  const handleTooltipMouseLeave = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (
      event.relatedTarget instanceof Node &&
      hoveredTargetRef.current?.element.contains(event.relatedTarget)
    ) {
      return;
    }
    scheduleFinishHover();
  };

  return createPortal(
    <div
      ref={tooltipRef}
      id={TOOLTIP_ID}
      role="tooltip"
      data-testid="truncated-text-tooltip"
      className="fixed z-[140] max-h-[calc(100vh-1.5rem)] max-w-[min(42rem,calc(100vw-1.5rem))] overflow-auto whitespace-pre-wrap rounded-lg border border-white/10 bg-stone-950 px-3 py-2 text-xs leading-5 text-white shadow-xl [overflow-wrap:anywhere]"
      style={{
        left: position?.left ?? VIEWPORT_PADDING,
        top: position?.top ?? VIEWPORT_PADDING,
        visibility: position ? "visible" : "hidden",
      }}
      onMouseEnter={clearHideDelay}
      onMouseLeave={handleTooltipMouseLeave}
    >
      {activeTarget.text}
    </div>,
    document.body,
  );
};
