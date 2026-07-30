import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  type CSSProperties,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";

type FloatingMenuPortalProps = {
  open: boolean;
  anchorRef: RefObject<HTMLElement | null>;
  align?: "left" | "right";
  minWidth?: number;
  maxHeight?: number;
  testId?: string;
  className?: string;
  children: ReactNode;
  onClose: () => void;
};

const VIEWPORT_MARGIN = 12;
const MENU_GAP = 8;
const DEFAULT_MAX_HEIGHT = 320;

const getHiddenMenuStyle = (
  minWidth: number,
  maxHeight: number,
): CSSProperties => ({
  left: 0,
  top: 0,
  minWidth,
  maxHeight,
  visibility: "hidden",
});

type FloatingMenuDomStyle = {
  left: string;
  top: string;
  minWidth: string;
  maxHeight: string;
  transform: string;
  visibility: "hidden" | "visible";
};

const toPx = (value: number) => `${value}px`;

const getPositionedMenuStyle = (
  anchor: HTMLElement | null,
  align: "left" | "right",
  minWidth: number,
  maxHeight: number,
): FloatingMenuDomStyle => {
  if (!anchor || typeof window === "undefined") {
    return {
      left: "0px",
      top: "0px",
      minWidth: toPx(minWidth),
      maxHeight: toPx(maxHeight),
      transform: "",
      visibility: "hidden",
    };
  }

  const rect = anchor.getBoundingClientRect();
  const menuWidth = Math.max(minWidth, rect.width);
  const viewportWidth = window.innerWidth || 1024;
  const viewportHeight = window.innerHeight || 768;
  const preferredLeft = align === "right" ? rect.right - menuWidth : rect.left;
  const left = Math.min(
    Math.max(VIEWPORT_MARGIN, preferredLeft),
    Math.max(VIEWPORT_MARGIN, viewportWidth - menuWidth - VIEWPORT_MARGIN),
  );
  const belowSpace = viewportHeight - rect.bottom - VIEWPORT_MARGIN;
  const aboveSpace = rect.top - VIEWPORT_MARGIN;
  const openUpward = belowSpace < 220 && aboveSpace > belowSpace;
  const availableHeight = openUpward ? aboveSpace - MENU_GAP : belowSpace - MENU_GAP;

  return {
    left: toPx(left),
    top: toPx(openUpward ? rect.top - MENU_GAP : rect.bottom + MENU_GAP),
    minWidth: toPx(menuWidth),
    maxHeight: toPx(Math.max(160, Math.min(maxHeight, availableHeight))),
    transform: openUpward ? "translateY(-100%)" : "",
    visibility: "visible",
  };
};

export const FloatingMenuPortal = ({
  open,
  anchorRef,
  align = "left",
  minWidth = 180,
  maxHeight = DEFAULT_MAX_HEIGHT,
  testId,
  className,
  children,
  onClose,
}: FloatingMenuPortalProps) => {
  const menuRef = useRef<HTMLDivElement | null>(null);
  const positionMenu = useCallback(() => {
    const menu = menuRef.current;
    if (!menu) {
      return;
    }

    Object.assign(
      menu.style,
      getPositionedMenuStyle(anchorRef.current, align, minWidth, maxHeight),
    );
  }, [align, anchorRef, maxHeight, minWidth]);

  useLayoutEffect(() => {
    if (!open) {
      return;
    }

    positionMenu();
    const frame = window.requestAnimationFrame(positionMenu);

    window.addEventListener("resize", positionMenu);

    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", positionMenu);
    };
  }, [open, positionMenu]);

  useEffect(() => {
    if (!open) {
      return;
    }

    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        menuRef.current?.contains(target) ||
        anchorRef.current?.contains(target)
      ) {
        return;
      }

      onClose();
    };

    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [anchorRef, onClose, open]);

  useEffect(() => {
    if (!open) {
      return;
    }

    document.addEventListener("scroll", positionMenu, true);

    return () => {
      document.removeEventListener("scroll", positionMenu, true);
    };
  }, [open, positionMenu]);

  if (!open || typeof document === "undefined") {
    return null;
  }

  return createPortal(
    <div
      ref={menuRef}
      data-testid={testId}
      className={clsx(
        "fixed z-[80] overflow-y-auto rounded-2xl border border-stone-200 bg-white p-2 shadow-[0_18px_40px_-24px_rgba(41,37,36,0.38)]",
        className,
      )}
      style={getHiddenMenuStyle(minWidth, maxHeight)}
    >
      {children}
    </div>,
    document.body,
  );
};
