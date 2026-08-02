import { useEffect, useMemo, useState } from "react";
import {
  clampPageSize,
  getPageForPageSizeChange,
  getPageItems,
  getStoredPageSize,
  getTotalPages,
  PAGE_SIZE,
  setStoredPageSize,
  type PaginationChange,
} from "@/lib/pagination";

type TaskDetailItem = {
  status: string;
};

export const useTaskDetailItems = <T extends TaskDetailItem>(
  items: T[],
  resetKey: number | string | null,
  options?: {
    initialPageSize?: number;
    pageSizeStorageKey?: string;
  },
) => {
  const [statusFilter, setStatusFilterState] = useState<"all" | T["status"]>(
    "all",
  );
  const [page, setPage] = useState(1);
  const [pageSize, setPageSizeState] = useState(() => {
    const initialPageSize = clampPageSize(options?.initialPageSize ?? PAGE_SIZE);
    return options?.pageSizeStorageKey
      ? getStoredPageSize(options.pageSizeStorageKey, initialPageSize)
      : initialPageSize;
  });

  const filteredItems = useMemo(
    () =>
      statusFilter === "all"
        ? items
        : items.filter((item) => item.status === statusFilter),
    [items, statusFilter],
  );
  const safePage = Math.min(
    page,
    getTotalPages(filteredItems.length, pageSize),
  );
  const visibleItems = useMemo(
    () => getPageItems(filteredItems, safePage, pageSize),
    [filteredItems, pageSize, safePage],
  );

  useEffect(() => {
    setStatusFilterState("all");
    setPage(1);
  }, [resetKey]);

  useEffect(() => {
    setPage((currentPage) =>
      Math.min(currentPage, getTotalPages(filteredItems.length, pageSize)),
    );
  }, [filteredItems.length, pageSize]);

  const setStatusFilter = (status: "all" | T["status"]) => {
    setStatusFilterState(status);
    setPage(1);
  };

  const setPageSize = (nextPageSize: number) => {
    const safePageSize = clampPageSize(nextPageSize);
    setPage((currentPage) =>
      getPageForPageSizeChange({
        page: currentPage,
        pageSize,
        nextPageSize: safePageSize,
        totalCount: filteredItems.length,
      }),
    );
    setPageSizeState(safePageSize);
    if (options?.pageSizeStorageKey) {
      setStoredPageSize(options.pageSizeStorageKey, safePageSize);
    }
  };

  const setPagination = (change: PaginationChange) => {
    const safePageSize = clampPageSize(change.pageSize);
    const nextPage = Math.min(
      Number.isFinite(change.page)
        ? Math.max(1, Math.trunc(change.page))
        : 1,
      getTotalPages(filteredItems.length, safePageSize),
    );
    setPage(nextPage);
    setPageSizeState(safePageSize);
    if (options?.pageSizeStorageKey) {
      setStoredPageSize(options.pageSizeStorageKey, safePageSize);
    }
  };

  return {
    filteredItems,
    page: safePage,
    pageSize,
    setPage,
    setPageSize,
    setPagination,
    setStatusFilter,
    statusFilter,
    visibleItems,
  };
};
