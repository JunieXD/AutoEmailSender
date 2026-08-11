import { useEffect } from "react";
import { getAppScrollContainer } from "./appScrollContainer";

let activeScrollLockCount = 0;
let originalBodyOverflow = "";
let originalDocumentOverflow = "";
let lockedScrollContainer: HTMLElement | null = null;
let originalScrollContainerOverflowY = "";

const acquireDocumentScrollLock = () => {
  if (typeof document === "undefined") {
    return () => undefined;
  }

  if (activeScrollLockCount === 0) {
    originalBodyOverflow = document.body.style.overflow;
    originalDocumentOverflow = document.documentElement.style.overflow;
    lockedScrollContainer = getAppScrollContainer();
    originalScrollContainerOverflowY = lockedScrollContainer?.style.overflowY ?? "";
    document.body.style.overflow = "hidden";
    document.documentElement.style.overflow = "hidden";
    if (lockedScrollContainer) {
      lockedScrollContainer.style.overflowY = "hidden";
    }
  }
  activeScrollLockCount += 1;

  let released = false;
  return () => {
    if (released) {
      return;
    }
    released = true;
    activeScrollLockCount = Math.max(0, activeScrollLockCount - 1);
    if (activeScrollLockCount === 0) {
      document.body.style.overflow = originalBodyOverflow;
      document.documentElement.style.overflow = originalDocumentOverflow;
      if (lockedScrollContainer) {
        lockedScrollContainer.style.overflowY = originalScrollContainerOverflowY;
      }
      lockedScrollContainer = null;
      originalScrollContainerOverflowY = "";
    }
  };
};

export const useDocumentScrollLock = (locked: boolean) => {
  useEffect(() => {
    if (!locked) {
      return undefined;
    }
    return acquireDocumentScrollLock();
  }, [locked]);
};
