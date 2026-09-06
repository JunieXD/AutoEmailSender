import { useCallback, useRef } from "react";

type ItemsPage<Item> = {
  items: Item[];
  total_count: number;
  has_more: boolean;
};

type Options<Item, Status extends string> = {
  page: number;
  pageSize: number;
  status: Status | "all";
  cacheSize: number;
  fetchPage: (
    jobId: number,
    query: {
      cursor: number;
      limit: number;
      status: Status | null;
    },
  ) => Promise<ItemsPage<Item>>;
  setItems: (items: Item[]) => void;
  setTotalCount: (count: number) => void;
  setLoading: (loading: boolean) => void;
  notifyError: (title: string, message: string) => void;
  errorTitle: string;
};

/** Shared paging lifecycle for matching and enrichment job details. */
export function useJobItemsPage<Item, Status extends string>({
  page,
  pageSize,
  status,
  cacheSize,
  fetchPage,
  setItems,
  setTotalCount,
  setLoading,
  notifyError,
  errorTitle,
}: Options<Item, Status>) {
  const cacheRef = useRef(new Map<string, ItemsPage<Item>>());
  const requestIdRef = useRef(0);
  const lastErrorRef = useRef<string | null>(null);
  const cachePage = useCallback(
    (key: string, value: ItemsPage<Item>) => {
      const cache = cacheRef.current;
      cache.delete(key);
      cache.set(key, value);
      while (cache.size > cacheSize) {
        const oldest = cache.keys().next().value;
        if (oldest === undefined) break;
        cache.delete(oldest);
      }
    },
    [cacheSize],
  );

  const prefetch = useCallback(
    async (jobId: number, cursor: number) => {
      if (cursor < 0) return;
      const key = JSON.stringify([jobId, cursor, pageSize, status]);
      if (cacheRef.current.has(key)) return;
      try {
        cachePage(
          key,
          await fetchPage(jobId, {
            cursor,
            limit: pageSize,
            status: status === "all" ? null : status,
          }),
        );
      } catch {
        // Speculative loads must not interrupt the visible page.
      }
    },
    [cachePage, fetchPage, pageSize, status],
  );

  const load = useCallback(
    async (jobId: number) => {
      const requestId = ++requestIdRef.current;
      const cursor = (page - 1) * pageSize;
      const key = JSON.stringify([jobId, cursor, pageSize, status]);
      const cached = cacheRef.current.get(key);
      if (cached) {
        setItems(cached.items);
        setTotalCount(cached.total_count);
      }
      setLoading(!cached);
      try {
        const data = await fetchPage(jobId, {
          cursor,
          limit: pageSize,
          status: status === "all" ? null : status,
        });
        cachePage(key, data);
        if (requestIdRef.current !== requestId) return;
        setItems(data.items);
        setTotalCount(data.total_count);
        lastErrorRef.current = null;
        if (data.has_more) void prefetch(jobId, cursor + pageSize);
        if (cursor > 0) void prefetch(jobId, cursor - pageSize);
      } catch (error) {
        if (requestIdRef.current !== requestId) return;
        const message = error instanceof Error ? error.message : errorTitle;
        if (lastErrorRef.current !== message) {
          notifyError(errorTitle, message);
          lastErrorRef.current = message;
        }
      } finally {
        if (requestIdRef.current === requestId) setLoading(false);
      }
    },
    [
      page,
      pageSize,
      status,
      fetchPage,
      cachePage,
      prefetch,
      setItems,
      setTotalCount,
      setLoading,
      notifyError,
      errorTitle,
    ],
  );

  const invalidate = useCallback(() => {
    requestIdRef.current += 1;
  }, []);
  const resetError = useCallback(() => {
    lastErrorRef.current = null;
  }, []);
  return { load, invalidate, resetError };
}
