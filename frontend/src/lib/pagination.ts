export const PAGE_SIZE = 10;
export const PAGE_SIZE_OPTIONS = [10, 20, 50] as const;
export const MIN_PAGE_SIZE = 1;
export const MAX_PAGE_SIZE = 100;

export type PaginationChangeReason = "page" | "page-size";

export type PaginationChange = {
  page: number;
  pageSize: number;
  reason: PaginationChangeReason;
};

export type PaginationItem =
  | number
  | "ellipsis-start"
  | "ellipsis-end";

export const clampPageSize = (value: number, fallback = PAGE_SIZE) => {
  const safeFallback = Number.isFinite(fallback)
    ? Math.min(MAX_PAGE_SIZE, Math.max(MIN_PAGE_SIZE, Math.trunc(fallback)))
    : PAGE_SIZE;
  if (!Number.isFinite(value)) {
    return safeFallback;
  }

  return Math.min(MAX_PAGE_SIZE, Math.max(MIN_PAGE_SIZE, Math.trunc(value)));
};

export const getStoredPageSize = (storageKey: string, fallback = PAGE_SIZE) => {
  const safeFallback = clampPageSize(fallback);
  try {
    const rawValue = globalThis.localStorage.getItem(storageKey);
    if (!rawValue) {
      return safeFallback;
    }
    const value = Number(rawValue);
    if (
      !Number.isInteger(value) ||
      value < MIN_PAGE_SIZE ||
      value > MAX_PAGE_SIZE
    ) {
      return safeFallback;
    }
    return value;
  } catch {
    return safeFallback;
  }
};

export const setStoredPageSize = (storageKey: string, pageSize: number) => {
  try {
    globalThis.localStorage.setItem(storageKey, String(clampPageSize(pageSize)));
  } catch {
    // Losing a display preference should not break pagination.
  }
};

export const getTotalPages = (totalCount: number, pageSize = PAGE_SIZE) => {
  const safeTotalCount = Number.isFinite(totalCount)
    ? Math.max(0, Math.trunc(totalCount))
    : 0;
  const safePageSize = clampPageSize(pageSize);
  return Math.max(1, Math.ceil(safeTotalCount / safePageSize));
};

export const getPageItems = <T,>(
  items: T[],
  page: number,
  pageSize = PAGE_SIZE,
) => {
  const safePageSize = clampPageSize(pageSize);
  const safePage = Number.isFinite(page)
    ? Math.min(
        getTotalPages(items.length, safePageSize),
        Math.max(1, Math.trunc(page)),
      )
    : 1;
  const startIndex = (safePage - 1) * safePageSize;
  return items.slice(startIndex, startIndex + safePageSize);
};

export const getPageForPageSizeChange = ({
  page,
  pageSize,
  nextPageSize,
  totalCount,
}: {
  page: number;
  pageSize: number;
  nextPageSize: number;
  totalCount: number;
}) => {
  const safePage = Number.isFinite(page) ? Math.max(1, Math.trunc(page)) : 1;
  const safePageSize = clampPageSize(pageSize);
  const safeNextPageSize = clampPageSize(nextPageSize);
  const firstVisibleItemIndex = (safePage - 1) * safePageSize;
  const nextPage = Math.floor(firstVisibleItemIndex / safeNextPageSize) + 1;

  return Math.min(
    nextPage,
    getTotalPages(totalCount, safeNextPageSize),
  );
};

export const getPaginationItems = (
  page: number,
  totalPages: number,
  siblingCount = 1,
): PaginationItem[] => {
  const safeTotalPages = Number.isFinite(totalPages)
    ? Math.max(1, Math.trunc(totalPages))
    : 1;
  const safePage = Number.isFinite(page)
    ? Math.min(safeTotalPages, Math.max(1, Math.trunc(page)))
    : 1;
  const safeSiblingCount = Number.isFinite(siblingCount)
    ? Math.max(0, Math.trunc(siblingCount))
    : 1;
  const maxVisibleItems = safeSiblingCount * 2 + 5;

  if (safeTotalPages <= maxVisibleItems) {
    return Array.from({ length: safeTotalPages }, (_, index) => index + 1);
  }

  const edgeWindowSize = safeSiblingCount * 2 + 1;

  if (safePage <= safeSiblingCount + 3) {
    return [
      ...Array.from(
        { length: Math.max(edgeWindowSize, safePage) },
        (_, index) => index + 1,
      ),
      "ellipsis-end",
      safeTotalPages,
    ];
  }

  if (safePage >= safeTotalPages - safeSiblingCount - 2) {
    const trailingWindowStart = Math.min(
      safePage,
      safeTotalPages - edgeWindowSize + 1,
    );
    return [
      1,
      "ellipsis-start",
      ...Array.from(
        { length: safeTotalPages - trailingWindowStart + 1 },
        (_, index) => trailingWindowStart + index,
      ),
    ];
  }

  return [
    1,
    "ellipsis-start",
    ...Array.from(
      { length: safeSiblingCount * 2 + 1 },
      (_, index) => safePage - safeSiblingCount + index,
    ),
    "ellipsis-end",
    safeTotalPages,
  ];
};
