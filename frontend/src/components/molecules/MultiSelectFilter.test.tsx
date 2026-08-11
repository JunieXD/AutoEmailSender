import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MultiSelectFilter } from "@/components/molecules/MultiSelectFilter";

const options = ["示例大学", "第二大学", "第三学院"];

const renderFilter = (
  selectedValues: string[] = [],
  onChange = vi.fn(),
) => {
  render(
    <MultiSelectFilter
      label="学校"
      allLabel="全部学校"
      selectedValues={selectedValues}
      options={options}
      onChange={onChange}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "学校：全部学校" }));
  return onChange;
};

describe("MultiSelectFilter", () => {
  it("keeps the content popover below the sticky header layer", () => {
    renderFilter();

    const menu = screen.getByRole("listbox").closest(".absolute");
    expect(menu).toHaveClass("z-40");
    expect(menu).not.toHaveClass("z-50");
  });

  it("opens upward when there is not enough room below the trigger", () => {
    const originalInnerHeight = window.innerHeight;
    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      value: 600,
    });

    try {
      render(
        <MultiSelectFilter
          label="学校"
          allLabel="全部学校"
          selectedValues={[]}
          options={options}
          onChange={vi.fn()}
        />,
      );
      const trigger = screen.getByRole("button", { name: "学校：全部学校" });
      vi.spyOn(trigger, "getBoundingClientRect").mockReturnValue({
        x: 0,
        y: 500,
        width: 240,
        height: 40,
        top: 500,
        right: 240,
        bottom: 540,
        left: 0,
        toJSON: () => ({}),
      });

      fireEvent.click(trigger);

      const menu = screen.getByRole("listbox").closest(".absolute");
      expect(menu).toHaveClass("bottom-[calc(100%+0.45rem)]");
      expect(menu).not.toHaveClass("top-[calc(100%+0.45rem)]");
      expect(menu).toHaveStyle({ maxHeight: "440px" });
      expect(screen.getByRole("textbox", { name: "搜索学校选项" })).toHaveFocus();
    } finally {
      Object.defineProperty(window, "innerHeight", {
        configurable: true,
        value: originalInnerHeight,
      });
    }
  });

  it("keeps an upward popover below the sticky app header", () => {
    const originalInnerHeight = window.innerHeight;
    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      value: 600,
    });

    try {
      render(
        <>
          <nav data-app-header="true" />
          <MultiSelectFilter
            label="状态"
            allLabel="全部状态"
            selectedValues={[]}
            options={options}
            onChange={vi.fn()}
          />
        </>,
      );
      const header = document.querySelector<HTMLElement>("[data-app-header]");
      const trigger = screen.getByRole("button", { name: "状态：全部状态" });
      vi.spyOn(header!, "getBoundingClientRect").mockReturnValue({
        x: 0,
        y: 0,
        width: 1200,
        height: 120,
        top: 0,
        right: 1200,
        bottom: 120,
        left: 0,
        toJSON: () => ({}),
      });
      vi.spyOn(trigger, "getBoundingClientRect").mockReturnValue({
        x: 0,
        y: 500,
        width: 240,
        height: 40,
        top: 500,
        right: 240,
        bottom: 540,
        left: 0,
        toJSON: () => ({}),
      });

      fireEvent.click(trigger);

      const menu = screen.getByRole("listbox").closest(".absolute");
      expect(menu).toHaveClass("bottom-[calc(100%+0.45rem)]");
      expect(menu).toHaveStyle({ maxHeight: "372px" });
    } finally {
      Object.defineProperty(window, "innerHeight", {
        configurable: true,
        value: originalInnerHeight,
      });
    }
  });

  it("renders an unrestricted filter as every option selected", () => {
    renderFilter();

    expect(screen.getByText("3 项 · 已选 3 项")).toBeInTheDocument();
    options.forEach((option) => {
      expect(screen.getByRole("option", { name: option })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });
    expect(
      screen.getByRole("button", { name: "取消全选" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("searches options and inverts only the current results", () => {
    const onChange = renderFilter();

    fireEvent.change(screen.getByRole("textbox", { name: "搜索学校选项" }), {
      target: { value: "第二" },
    });

    expect(screen.getByRole("option", { name: "第二大学" })).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "示例大学" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("1 项 · 已选 1 项")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "反选" }));

    expect(screen.getByRole("option", { name: "第二大学" })).toHaveAttribute(
      "aria-selected",
      "false",
    );
    expect(screen.getByText("1 项 · 已选 2 项")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "应用" }));

    expect(onChange).toHaveBeenCalledWith(["示例大学", "第三学院"]);
  });

  it("applies searched options when the filter was unrestricted", () => {
    const onChange = renderFilter();

    fireEvent.change(screen.getByRole("textbox", { name: "搜索学校选项" }), {
      target: { value: "大学" },
    });

    expect(screen.getByText("2 项 · 已选 2 项")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "应用" }));

    expect(onChange).toHaveBeenCalledWith(["示例大学", "第二大学"]);
  });

  it("applies searched options with Enter outside IME composition", () => {
    const onChange = renderFilter();
    const searchInput = screen.getByRole("textbox", {
      name: "搜索学校选项",
    });

    fireEvent.change(searchInput, { target: { value: "大学" } });
    fireEvent.keyDown(searchInput, {
      key: "Enter",
      code: "Enter",
      isComposing: true,
    });
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.keyDown(searchInput, { key: "Enter", code: "Enter" });

    expect(onChange).toHaveBeenCalledWith(["示例大学", "第二大学"]);
  });

  it("preserves an existing partial selection when only searching", () => {
    const onChange = vi.fn();
    render(
      <MultiSelectFilter
        label="学校"
        allLabel="全部学校"
        selectedValues={["示例大学"]}
        options={options}
        onChange={onChange}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "学校：示例大学" }),
    );

    fireEvent.change(screen.getByRole("textbox", { name: "搜索学校选项" }), {
      target: { value: "第二" },
    });
    fireEvent.click(screen.getByRole("button", { name: "应用" }));

    expect(onChange).toHaveBeenCalledWith(["示例大学"]);
  });

  it("supports clearing the draft before selecting a small subset", () => {
    const onChange = renderFilter();

    fireEvent.click(
      screen.getByRole("button", { name: "取消全选" }),
    );

    expect(screen.getByText("至少保留一项")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "应用" })).toBeDisabled();

    fireEvent.click(screen.getByRole("option", { name: "示例大学" }));
    fireEvent.click(screen.getByRole("button", { name: "应用" }));

    expect(onChange).toHaveBeenCalledWith(["示例大学"]);
  });

  it("normalizes an explicitly selected full set back to unrestricted", () => {
    const onChange = vi.fn();
    render(
      <MultiSelectFilter
        label="学校"
        allLabel="全部学校"
        selectedValues={["示例大学"]}
        options={options}
        onChange={onChange}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "学校：示例大学" }),
    );

    fireEvent.click(screen.getByRole("button", { name: "全选当前结果" }));
    fireEvent.click(screen.getByRole("button", { name: "应用" }));

    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("keeps hidden selections while inverting searched options", () => {
    const onChange = vi.fn();
    render(
      <MultiSelectFilter
        label="学校"
        allLabel="全部学校"
        selectedValues={["示例大学"]}
        options={options}
        onChange={onChange}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "学校：示例大学" }),
    );
    fireEvent.change(screen.getByRole("textbox", { name: "搜索学校选项" }), {
      target: { value: "第二" },
    });
    fireEvent.click(screen.getByRole("button", { name: "反选" }));
    fireEvent.click(screen.getByRole("button", { name: "应用" }));

    expect(onChange).toHaveBeenCalledWith(["示例大学", "第二大学"]);
  });

  it("discards draft changes when cancelled", () => {
    const onChange = renderFilter();

    fireEvent.click(screen.getByRole("button", { name: "反选" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(onChange).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "学校：全部学校" }),
    ).toHaveFocus();
  });

  it("searches by display labels while preserving stable option values", () => {
    const onChange = vi.fn();
    render(
      <MultiSelectFilter
        label="标签"
        allLabel="全部标签"
        selectedValues={[]}
        options={["11", "22"]}
        optionLabels={{ "11": "重点联系", "22": "暂缓联系" }}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "标签：全部标签" }));
    fireEvent.change(screen.getByRole("textbox", { name: "搜索标签选项" }), {
      target: { value: "重点" },
    });
    fireEvent.click(screen.getByRole("button", { name: "反选" }));
    fireEvent.click(screen.getByRole("button", { name: "应用" }));

    expect(onChange).toHaveBeenCalledWith(["22"]);
  });
});
