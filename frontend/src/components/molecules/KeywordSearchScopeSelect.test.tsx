import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { KeywordSearchScopeSelect } from "./KeywordSearchScopeSelect";

const options = [
  { value: "name", label: "姓名" },
  { value: "title", label: "职称" },
  { value: "school", label: "学院" },
];

describe("KeywordSearchScopeSelect", () => {
  it("shows all fields when every option is selected", () => {
    render(
      <KeywordSearchScopeSelect
        label="搜索范围"
        options={options}
        selectedValues={["name", "title", "school"]}
        onChange={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("button", { name: /搜索范围：选择字段：全部字段/ }),
    ).toBeInTheDocument();
  });

  it("shows selected count and updates removable options", () => {
    const onToggle = vi.fn();
    render(
      <KeywordSearchScopeSelect
        label="搜索范围"
        options={options}
        selectedValues={["name", "title"]}
        onChange={(nextValues) => onToggle(nextValues)}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /搜索范围：选择字段：已选 2 项/ }),
    );
    fireEvent.click(screen.getByRole("option", { name: "职称" }));

    expect(onToggle).toHaveBeenCalledWith(["name"]);
  });

  it("shows singular selected text and selects every option", () => {
    const onChange = vi.fn();
    render(
      <KeywordSearchScopeSelect
        label="搜索范围"
        options={options}
        selectedValues={["name"]}
        onChange={onChange}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /搜索范围：选择字段：已选一项/ }),
    );
    fireEvent.click(screen.getByRole("button", { name: "全部选择" }));

    expect(onChange).toHaveBeenCalledWith(["name", "title", "school"]);
  });

  it("shows the retention hint and keeps at least one selected field", () => {
    const onChange = vi.fn();
    render(
      <KeywordSearchScopeSelect
        label="搜索范围"
        options={options}
        selectedValues={["name"]}
        onChange={onChange}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /搜索范围：选择字段：已选一项/ }),
    );
    expect(screen.getByText("至少保留一项")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("option", { name: "姓名" }));

    expect(onChange).not.toHaveBeenCalled();
  });

  it("clears the visual selection without committing when 全部取消 is clicked", () => {
    const onChange = vi.fn();
    render(
      <KeywordSearchScopeSelect
        label="搜索范围"
        options={options}
        selectedValues={["name", "title", "school"]}
        onChange={onChange}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /搜索范围：选择字段：全部字段/ }),
    );
    fireEvent.click(screen.getByRole("button", { name: "全部取消" }));

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText(/选择一项以应用/)).toBeInTheDocument();
    options.forEach((option) => {
      expect(screen.getByRole("option", { name: option.label })).toHaveAttribute(
        "aria-selected",
        "false",
      );
    });
  });

  it("commits a single value when picking an option in draft mode", () => {
    const onChange = vi.fn();
    render(
      <KeywordSearchScopeSelect
        label="搜索范围"
        options={options}
        selectedValues={["name", "title", "school"]}
        onChange={onChange}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /搜索范围：选择字段：全部字段/ }),
    );
    fireEvent.click(screen.getByRole("button", { name: "全部取消" }));
    fireEvent.click(screen.getByRole("option", { name: "姓名" }));

    expect(onChange).toHaveBeenCalledWith(["name"]);
  });

  it("commits all options when 全部选择 is clicked in draft mode", () => {
    const onChange = vi.fn();
    render(
      <KeywordSearchScopeSelect
        label="搜索范围"
        options={options}
        selectedValues={["name"]}
        onChange={onChange}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /搜索范围：选择字段：已选一项/ }),
    );
    fireEvent.click(screen.getByRole("button", { name: "全部取消" }));
    fireEvent.click(screen.getByRole("button", { name: "全部选择" }));

    expect(onChange).toHaveBeenCalledWith(["name", "title", "school"]);
  });

  it("discards the draft when the panel is closed without selection", () => {
    const onChange = vi.fn();
    render(
      <KeywordSearchScopeSelect
        label="搜索范围"
        options={options}
        selectedValues={["name", "title"]}
        onChange={onChange}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /搜索范围：选择字段：已选 2 项/ }),
    );
    fireEvent.click(screen.getByRole("button", { name: "全部取消" }));
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(
      screen.getByRole("button", { name: /搜索范围：选择字段：已选 2 项/ }),
    );

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("option", { name: "姓名" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("option", { name: "职称" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("至少保留一项")).toBeInTheDocument();
  });

  it("disables 全部取消 when nothing is currently displayed as selected", () => {
    render(
      <KeywordSearchScopeSelect
        label="搜索范围"
        options={options}
        selectedValues={["name"]}
        onChange={vi.fn()}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /搜索范围：选择字段：已选一项/ }),
    );
    fireEvent.click(screen.getByRole("button", { name: "全部取消" }));
    expect(screen.getByRole("button", { name: "全部取消" })).toBeDisabled();
  });

  it("clears the draft when selectedValues prop changes", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <KeywordSearchScopeSelect
        label="搜索范围"
        options={options}
        selectedValues={["name", "title"]}
        onChange={onChange}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /搜索范围：选择字段：已选 2 项/ }),
    );
    fireEvent.click(screen.getByRole("button", { name: "全部取消" }));

    rerender(
      <KeywordSearchScopeSelect
        label="搜索范围"
        options={options}
        selectedValues={["school"]}
        onChange={onChange}
      />,
    );

    expect(screen.getByRole("option", { name: "学院" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("至少保留一项")).toBeInTheDocument();
  });
});
