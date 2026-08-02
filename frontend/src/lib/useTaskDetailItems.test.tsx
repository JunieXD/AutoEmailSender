import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { useTaskDetailItems } from "./useTaskDetailItems";

type ItemStatus = "succeeded" | "failed";

type Item = {
  id: number;
  status: ItemStatus;
};

const items: Item[] = Array.from({ length: 12 }, (_, index) => ({
  id: index + 1,
  status: index === 11 ? "failed" : "succeeded",
}));

describe("useTaskDetailItems", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("filters and paginates local task details with a clamped custom page size", () => {
    const { result, rerender } = renderHook(
      ({ resetKey }) => useTaskDetailItems(items, resetKey),
      { initialProps: { resetKey: 1 } },
    );

    expect(result.current.pageSize).toBe(10);
    expect(result.current.visibleItems.map((item) => item.id)).toEqual([
      1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
    ]);

    act(() => result.current.setPage(2));
    expect(result.current.visibleItems.map((item) => item.id)).toEqual([11, 12]);

    act(() => result.current.setStatusFilter("failed"));
    expect(result.current.page).toBe(1);
    expect(result.current.filteredItems.map((item) => item.id)).toEqual([12]);

    act(() => result.current.setPageSize(101));
    expect(result.current.pageSize).toBe(100);

    rerender({ resetKey: 2 });
    expect(result.current.statusFilter).toBe("all");
    expect(result.current.page).toBe(1);
    expect(result.current.pageSize).toBe(100);
  });

  it("updates detail pagination atomically and persists its page size", () => {
    const { result } = renderHook(() =>
      useTaskDetailItems(items, 1, {
        initialPageSize: 10,
        pageSizeStorageKey: "task-details:test",
      }),
    );

    act(() => {
      result.current.setPagination({
        page: 2,
        pageSize: 5,
        reason: "page-size",
      });
    });

    expect(result.current.page).toBe(2);
    expect(result.current.pageSize).toBe(5);
    expect(result.current.visibleItems.map((item) => item.id)).toEqual([
      6, 7, 8, 9, 10,
    ]);
    expect(window.localStorage.getItem("task-details:test")).toBe("5");
  });
});
