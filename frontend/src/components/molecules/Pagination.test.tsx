import { createRef, useRef, useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Pagination } from "./Pagination";
import type { PaginationChange } from "@/lib/pagination";

const scrollIntoView = vi.fn();

beforeEach(() => {
  scrollIntoView.mockReset();
  Object.defineProperty(window.HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: scrollIntoView,
  });
});

describe("Pagination", () => {
  it("uses numbered endpoints on standard layouts and reserves first/last for small screens", () => {
    render(
      <Pagination
        page={5}
        pageSize={10}
        totalCount={200}
        ariaLabel="导师分页"
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("首页")).toHaveClass("sm:hidden");
    expect(screen.getByRole("button", { name: "上一页" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "第 5 页" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("button", { name: "第 1 页" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "第 20 页" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "下一页" })).toBeEnabled();
    expect(screen.getByLabelText("尾页")).toHaveClass("sm:hidden");
    expect(screen.getByRole("button", { name: "跳页" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(
      screen.queryByRole("spinbutton", { name: "输入页码" }),
    ).not.toBeInTheDocument();
  });

  it("keeps explicit first/last controls without numbered pages in compact layouts", () => {
    render(
      <Pagination
        page={5}
        pageSize={10}
        totalCount={200}
        ariaLabel="弹窗分页"
        variant="compact"
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("首页")).not.toHaveClass("sm:hidden");
    expect(screen.getByLabelText("尾页")).not.toHaveClass("sm:hidden");
    expect(screen.queryByRole("button", { name: "第 5 页" })).not.toBeInTheDocument();
    expect(screen.getByText("5 / 20 页")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "跳页" })).not.toBeInTheDocument();
  });

  it("keeps the page-size control without showing disabled navigation for one page", () => {
    render(
      <Pagination
        page={1}
        pageSize={10}
        totalCount={3}
        ariaLabel="单页列表分页"
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "每页数量" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "首页" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "下一页" })).not.toBeInTheDocument();
  });

  it("emits an atomic change when using the numbered last-page endpoint", () => {
    const onChange = vi.fn();
    render(
      <Pagination
        page={2}
        pageSize={10}
        totalCount={95}
        ariaLabel="任务分页"
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "第 10 页" }));

    expect(onChange).toHaveBeenCalledWith({
      page: 10,
      pageSize: 10,
      reason: "page",
    });
  });

  it("keeps the first visible item in view when page size changes", () => {
    const onChange = vi.fn();
    render(
      <Pagination
        page={3}
        pageSize={10}
        totalCount={95}
        ariaLabel="任务分页"
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "每页数量" }));
    fireEvent.click(screen.getByRole("option", { name: "20" }));

    expect(onChange).toHaveBeenCalledWith({
      page: 2,
      pageSize: 20,
      reason: "page-size",
    });
  });

  it("focuses and scrolls to the list start only after controlled state commits", () => {
    const Harness = () => {
      const [pagination, setPagination] = useState({ page: 1, pageSize: 10 });
      const targetRef = useRef<HTMLHeadingElement | null>(null);
      return (
        <>
          <h2 ref={targetRef} tabIndex={-1}>
            列表开头
          </h2>
          <Pagination
            {...pagination}
            totalCount={30}
            ariaLabel="任务分页"
            focusTargetRef={targetRef}
            onChange={(change) => setPagination(change)}
          />
        </>
      );
    };
    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));

    const target = screen.getByRole("heading", { name: "列表开头" });
    expect(target).toHaveFocus();
    expect(scrollIntoView).toHaveBeenCalledWith({
      behavior: "auto",
      block: "start",
    });
  });

  it("does not move focus when a requested controlled change is not committed", () => {
    const targetRef = createRef<HTMLHeadingElement>();
    render(
      <>
        <h2 ref={targetRef} tabIndex={-1}>
          列表开头
        </h2>
        <Pagination
          page={1}
          pageSize={10}
          totalCount={30}
          ariaLabel="服务端分页"
          focusTargetRef={targetRef}
          onChange={vi.fn()}
        />
      </>,
    );

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));

    expect(screen.getByRole("heading", { name: "列表开头" })).not.toHaveFocus();
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("clears a pending focus request when an async page change finishes without committing", () => {
    const targetRef = createRef<HTMLHeadingElement>();
    const onChange = vi.fn();
    const renderPagination = (page: number, disabled: boolean) => (
      <>
        <h2 ref={targetRef} tabIndex={-1}>
          列表开头
        </h2>
        <Pagination
          page={page}
          pageSize={10}
          totalCount={30}
          ariaLabel="服务端分页"
          focusTargetRef={targetRef}
          disabled={disabled}
          onChange={onChange}
        />
      </>
    );
    const { rerender } = render(renderPagination(1, false));

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    rerender(renderPagination(1, true));
    rerender(renderPagination(1, false));
    rerender(renderPagination(2, false));

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("heading", { name: "列表开头" })).not.toHaveFocus();
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("does not move focus for background-controlled page updates", () => {
    const targetRef = createRef<HTMLHeadingElement>();
    const { rerender } = render(
      <>
        <h2 ref={targetRef} tabIndex={-1}>
          列表开头
        </h2>
        <Pagination
          page={1}
          pageSize={10}
          totalCount={30}
          ariaLabel="轮询列表分页"
          focusTargetRef={targetRef}
          onChange={vi.fn()}
        />
      </>,
    );

    rerender(
      <>
        <h2 ref={targetRef} tabIndex={-1}>
          列表开头
        </h2>
        <Pagination
          page={2}
          pageSize={10}
          totalCount={31}
          ariaLabel="轮询列表分页"
          focusTargetRef={targetRef}
          onChange={vi.fn()}
        />
      </>,
    );

    expect(screen.getByRole("heading", { name: "列表开头" })).not.toHaveFocus();
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("validates quick-jump pages instead of silently clamping them", () => {
    const onChange = vi.fn<(change: PaginationChange) => void>();
    render(
      <Pagination
        page={1}
        pageSize={10}
        totalCount={200}
        ariaLabel="任务分页"
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "跳页" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "输入页码" }), {
      target: { value: "21" },
    });
    fireEvent.click(screen.getByRole("button", { name: "跳转" }));

    expect(screen.getByRole("alert")).toHaveTextContent("请输入 1–20 之间的页码");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("closes the quick-jump popover after a valid jump", () => {
    const onChange = vi.fn<(change: PaginationChange) => void>();
    render(
      <Pagination
        page={1}
        pageSize={10}
        totalCount={200}
        ariaLabel="任务分页"
        onChange={onChange}
      />,
    );

    const trigger = screen.getByRole("button", { name: "跳页" });
    fireEvent.click(trigger);
    const input = screen.getByRole("spinbutton", { name: "输入页码" });
    expect(input).toHaveFocus();
    fireEvent.change(input, { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "跳转" }));

    expect(onChange).toHaveBeenCalledWith({
      page: 7,
      pageSize: 10,
      reason: "page",
    });
    expect(
      screen.queryByRole("spinbutton", { name: "输入页码" }),
    ).not.toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("returns focus to the quick-jump trigger when Escape closes it", () => {
    render(
      <Pagination
        page={1}
        pageSize={10}
        totalCount={200}
        ariaLabel="任务分页"
        onChange={vi.fn()}
      />,
    );

    const trigger = screen.getByRole("button", { name: "跳页" });
    fireEvent.click(trigger);
    fireEvent.keyDown(screen.getByRole("spinbutton", { name: "输入页码" }), {
      key: "Escape",
    });

    expect(
      screen.queryByRole("spinbutton", { name: "输入页码" }),
    ).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
