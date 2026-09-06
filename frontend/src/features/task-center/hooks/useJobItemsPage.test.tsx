import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useJobItemsPage } from "./useJobItemsPage";

type Page = { items: number[]; total_count: number; has_more: boolean };
const page = (value: number): Page => ({
  items: [value],
  total_count: 1,
  has_more: false,
});
const deferred = () => {
  let resolve!: (value: Page) => void;
  const promise = new Promise<Page>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
const options = (fetchPage: () => Promise<Page>) => ({
  page: 1,
  pageSize: 10,
  status: "all" as const,
  cacheSize: 2,
  fetchPage,
  setItems: vi.fn(),
  setTotalCount: vi.fn(),
  setLoading: vi.fn(),
  notifyError: vi.fn(),
  errorTitle: "加载失败",
});

describe("job detail paging", () => {
  it("ignores an older request and a response arriving after the dialog closes", async () => {
    const first = deferred();
    const second = deferred();
    const closed = deferred();
    const fetchPage = vi
      .fn()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
      .mockReturnValueOnce(closed.promise);
    const input = options(fetchPage);
    const { result } = renderHook(() => useJobItemsPage(input));
    let oldLoad!: Promise<void>;
    let currentLoad!: Promise<void>;
    act(() => {
      oldLoad = result.current.load(1);
      currentLoad = result.current.load(2);
    });
    await act(async () => {
      second.resolve(page(2));
      await currentLoad;
    });
    await act(async () => {
      first.resolve(page(1));
      await oldLoad;
    });
    expect(input.setItems.mock.calls).toEqual([[[2]]]);
    let closeLoad!: Promise<void>;
    act(() => {
      closeLoad = result.current.load(3);
      result.current.invalidate();
    });
    await act(async () => {
      closed.resolve(page(3));
      await closeLoad;
    });
    expect(input.setItems.mock.calls).toEqual([[[2]]]);
  });

  it("renders cached results while refreshing and evicts the oldest page", async () => {
    const refresh = deferred();
    const fetchPage = vi
      .fn()
      .mockResolvedValueOnce(page(1))
      .mockReturnValueOnce(refresh.promise)
      .mockResolvedValueOnce(page(2))
      .mockResolvedValueOnce(page(3))
      .mockResolvedValueOnce(page(10));
    const input = options(fetchPage);
    const { result } = renderHook(() => useJobItemsPage(input));
    await act(async () => {
      await result.current.load(1);
    });
    let load!: Promise<void>;
    act(() => {
      load = result.current.load(1);
    });
    expect(input.setItems).toHaveBeenLastCalledWith([1]);
    expect(input.setLoading).toHaveBeenLastCalledWith(false);
    await act(async () => {
      refresh.resolve(page(11));
      await load;
    });
    await act(async () => {
      await result.current.load(2);
      await result.current.load(3);
    });
    input.setLoading.mockClear();
    await act(async () => {
      await result.current.load(1);
    });
    expect(input.setLoading.mock.calls[0]).toEqual([true]);
  });

  it("deduplicates repeated errors and permits notification after recovery", async () => {
    const fetchPage = vi.fn().mockRejectedValue(new Error("offline"));
    const input = options(fetchPage);
    const { result } = renderHook(() => useJobItemsPage(input));
    await act(async () => {
      await result.current.load(1);
      await result.current.load(1);
    });
    expect(input.notifyError).toHaveBeenCalledTimes(1);
    fetchPage.mockResolvedValueOnce(page(1));
    await act(async () => {
      await result.current.load(1);
      await result.current.load(1);
    });
    expect(input.notifyError).toHaveBeenCalledTimes(2);
  });
});
