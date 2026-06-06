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
      screen.getByRole("button", { name: /搜索范围：全部字段/ }),
    ).toBeInTheDocument();
  });

  it("shows selected count and calls onToggle for removable options", () => {
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
      screen.getByRole("button", { name: /搜索范围：已选 2 项/ }),
    );
    fireEvent.click(screen.getByRole("option", { name: "职称" }));

    expect(onToggle).toHaveBeenCalledWith(["name"]);
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
      screen.getByRole("button", { name: /搜索范围：已选 1 项/ }),
    );
    expect(screen.getByText("至少保留最后一项")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("option", { name: "姓名" }));

    expect(onChange).not.toHaveBeenCalled();
  });
});
