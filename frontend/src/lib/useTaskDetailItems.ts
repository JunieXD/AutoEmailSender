import { useEffect, useMemo, useState } from "react";
import {
  clampPageSize,
  getPageItems,
  getTotalPages,
  PAGE_SIZE,
} from "@/lib/pagination";

type TaskDetailItem = {
  status: string;
};

export const useTaskDetailItems = <T extends TaskDetailItem>(
  items: T[],
  resetKey: number | string | null,
) => {
  const [statusFilter, setStatusFilterState] = useState<"all" | T["status"]>(
    "all",
  );
  const [page, setPage] = useState(1);
  const [pageSize, setPageSizeState] = useState(PAGE_SIZE);

  const filteredItems = useMemo(
    () =>
      statusFilter === "all"
        ? items
        : items.filter((item) => item.status === statusFilter),
    [items, statusFilter],
  );
  const visibleItems = useMemo(
    () => getPageItems(filteredItems, page, pageSize),
    [filteredItems, page, pageSize],
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
    setPageSizeState(clampPageSize(nextPageSize));
    setPage(1);
  };

  return {
    filteredItems,
    page,
    pageSize,
    setPage,
    setPageSize,
    setStatusFilter,
    statusFilter,
    visibleItems,
  };
};
