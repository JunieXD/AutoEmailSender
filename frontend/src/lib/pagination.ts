export const PAGE_SIZE = 10;
export const PAGE_SIZE_OPTIONS = [10, 20, 50] as const;
export const MIN_PAGE_SIZE = 1;
export const MAX_PAGE_SIZE = 100;

export const clampPageSize = (value: number, fallback = PAGE_SIZE) => {
  if (!Number.isFinite(value)) {
    return fallback;
  }

  return Math.min(MAX_PAGE_SIZE, Math.max(MIN_PAGE_SIZE, Math.trunc(value)));
};

export const getStoredPageSize = (storageKey: string, fallback = PAGE_SIZE) => {
  try {
    const rawValue = globalThis.localStorage.getItem(storageKey);
    if (!rawValue) {
      return fallback;
    }
    const value = Number(rawValue);
    if (
      !Number.isInteger(value) ||
      value < MIN_PAGE_SIZE ||
      value > MAX_PAGE_SIZE
    ) {
      return fallback;
    }
    return value;
  } catch {
    return fallback;
  }
};

export const setStoredPageSize = (storageKey: string, pageSize: number) => {
  try {
    globalThis.localStorage.setItem(storageKey, String(clampPageSize(pageSize)));
  } catch {
    // Losing a display preference should not break pagination.
  }
};

export const getTotalPages = (totalCount: number, pageSize = PAGE_SIZE) =>
  Math.max(1, Math.ceil(totalCount / pageSize));

export const getPageItems = <T,>(
  items: T[],
  page: number,
  pageSize = PAGE_SIZE,
) => {
  const startIndex = (page - 1) * pageSize;
  return items.slice(startIndex, startIndex + pageSize);
};
