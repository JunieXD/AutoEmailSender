import { useState } from "react";
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { usePageBounds, usePaginationState } from "./usePaginationState";

describe("usePaginationState", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("commits page and page size atomically and persists the page size", () => {
    window.localStorage.setItem("pagination:test", "20");
    const { result, unmount } = renderHook(() =>
      usePaginationState({ storageKey: "pagination:test" }),
    );

    expect(result.current.page).toBe(1);
    expect(result.current.pageSize).toBe(20);

    act(() => {
      result.current.onChange({
        page: 3,
        pageSize: 50,
        reason: "page-size",
      });
    });

    expect(result.current.page).toBe(3);
    expect(result.current.pageSize).toBe(50);
    expect(window.localStorage.getItem("pagination:test")).toBe("50");

    unmount();
    const restored = renderHook(() =>
      usePaginationState({ storageKey: "pagination:test" }),
    );
    expect(restored.result.current.pageSize).toBe(50);
  });

  it("normalizes invalid initial and committed values", () => {
    const { result } = renderHook(() =>
      usePaginationState({
        storageKey: "pagination:invalid",
        initialPage: Number.NaN,
      }),
    );

    act(() => {
      result.current.onChange({
        page: Number.NaN,
        pageSize: 1000,
        reason: "page-size",
      });
    });

    expect(result.current.page).toBe(1);
    expect(result.current.pageSize).toBe(100);
  });
});

describe("usePageBounds", () => {
  it("keeps the page in range as results and page size change", () => {
    const { result, rerender } = renderHook(({ total, size }) => {
      const [page, setPage] = useState(5);
      usePageBounds(setPage, total, size);
      return page;
    }, { initialProps: { total: 100, size: 10 } });

    expect(result.current).toBe(5);
    rerender({ total: 25, size: 10 });
    expect(result.current).toBe(3);
    rerender({ total: 25, size: 20 });
    expect(result.current).toBe(2);
    rerender({ total: 0, size: 20 });
    expect(result.current).toBe(1);
  });
});
