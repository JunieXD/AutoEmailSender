import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TruncatedTextTooltipProvider } from "./TruncatedTextTooltipProvider";

const setElementSize = (
  element: HTMLElement,
  dimensions: {
    clientWidth: number;
    scrollWidth: number;
    clientHeight?: number;
    scrollHeight?: number;
  },
) => {
  Object.defineProperties(element, {
    clientWidth: { configurable: true, value: dimensions.clientWidth },
    scrollWidth: { configurable: true, value: dimensions.scrollWidth },
    clientHeight: { configurable: true, value: dimensions.clientHeight ?? 20 },
    scrollHeight: { configurable: true, value: dimensions.scrollHeight ?? 20 },
  });
};

describe("TruncatedTextTooltipProvider", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it("shows the complete text after hovering a truncated line for one second", () => {
    render(
      <>
        <TruncatedTextTooltipProvider />
        <p className="truncate" title="这是一条完整的抓取执行日志">
          这是一条完整的抓取执行日志
        </p>
      </>,
    );
    const line = screen.getByText("这是一条完整的抓取执行日志");
    setElementSize(line, { clientWidth: 120, scrollWidth: 360 });

    fireEvent.mouseOver(line);
    expect(line).not.toHaveAttribute("title");

    act(() => vi.advanceTimersByTime(999));
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByRole("tooltip")).toHaveTextContent("这是一条完整的抓取执行日志");
    expect(line).toHaveAttribute("aria-describedby", "truncated-text-tooltip");
  });

  it("does not show a tooltip when the text fits", () => {
    render(
      <>
        <TruncatedTextTooltipProvider />
        <p className="truncate" title="短日志">
          短日志
        </p>
      </>,
    );
    const line = screen.getByText("短日志");
    setElementSize(line, { clientWidth: 120, scrollWidth: 120 });

    fireEvent.mouseOver(line);
    act(() => vi.advanceTimersByTime(1000));

    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("supports multi-line clamping and keyboard focus", () => {
    render(
      <>
        <TruncatedTextTooltipProvider />
        <button type="button" className="line-clamp-2">
          被两行省略的完整内容
        </button>
      </>,
    );
    const button = screen.getByRole("button");
    setElementSize(button, {
      clientWidth: 180,
      scrollWidth: 180,
      clientHeight: 40,
      scrollHeight: 72,
    });

    fireEvent.focusIn(button);

    expect(screen.getByRole("tooltip")).toHaveTextContent("被两行省略的完整内容");
  });

  it("hides the tooltip and restores an existing title after leaving", () => {
    render(
      <>
        <TruncatedTextTooltipProvider />
        <p className="truncate" title="完整标题">
          完整标题
        </p>
      </>,
    );
    const line = screen.getByText("完整标题");
    setElementSize(line, { clientWidth: 80, scrollWidth: 200 });

    fireEvent.mouseOver(line);
    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    fireEvent.mouseOut(line, { relatedTarget: document.body });
    act(() => vi.advanceTimersByTime(120));

    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    expect(line).toHaveAttribute("title", "完整标题");
  });
});
