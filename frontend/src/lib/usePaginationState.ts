import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from "react";
import {
  clampPageSize,
  getStoredPageSize,
  getTotalPages,
  PAGE_SIZE,
  setStoredPageSize,
  type PaginationChange,
} from "@/lib/pagination";

const normalizePage = (page: number) =>
  Number.isFinite(page) ? Math.max(1, Math.trunc(page)) : 1;

export const usePaginationState = ({
  storageKey,
  initialPage = 1,
  initialPageSize = PAGE_SIZE,
}: {
  storageKey: string;
  initialPage?: number;
  initialPageSize?: number;
}) => {
  const [page, setPage] = useState(() => normalizePage(initialPage));
  const [pageSize, setPageSize] = useState(() =>
    getStoredPageSize(storageKey, clampPageSize(initialPageSize)),
  );

  const onChange = useCallback(
    (change: PaginationChange) => {
      const safePageSize = clampPageSize(change.pageSize);
      setPage(normalizePage(change.page));
      setPageSize(safePageSize);
      setStoredPageSize(storageKey, safePageSize);
    },
    [storageKey],
  );

  return {
    page,
    pageSize,
    setPage,
    onChange,
  };
};

export function usePageBounds(
  setPage: Dispatch<SetStateAction<number>>,
  totalItems: number,
  pageSize: number,
) {
  useEffect(() => {
    setPage((page) => Math.min(page, getTotalPages(totalItems, pageSize)));
  }, [setPage, totalItems, pageSize]);
}
