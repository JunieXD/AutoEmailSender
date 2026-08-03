import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MultiSelectFilter } from "@/components/molecules/MultiSelectFilter";

describe("MultiSelectFilter", () => {
  it("shows all label when no values are selected", () => {
    render(
      <MultiSelectFilter
        label="学校"
        allLabel="全部学校"
        selectedValues={[]}
        options={["MIT", "Stanford"]}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "学校：全部学校" })).toBeInTheDocument();
  });

  it("opens options and toggles values", async () => {
    const onChange = vi.fn();

    render(
      <MultiSelectFilter
        label="学校"
        allLabel="全部学校"
        selectedValues={["MIT"]}
        options={["MIT", "Stanford"]}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "学校：MIT" }));

    const listbox = screen.getByRole("listbox", { name: "学校" });
    expect(within(listbox).getByRole("option", { name: "MIT" })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    fireEvent.click(within(listbox).getByRole("option", { name: "Stanford" }));
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "应用" }));

    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("keeps visible spacing between dropdown options", () => {
    render(
      <MultiSelectFilter
        label="学校"
        allLabel="全部学校"
        selectedValues={[]}
        options={["MIT", "Stanford"]}
        onChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "学校：全部学校" }));

    const listbox = screen.getByRole("listbox", { name: "学校" });
    expect(listbox).toHaveClass("flex", "flex-col", "gap-1");
  });

  it("summarizes multiple selected values and clears the filter", async () => {
    const onChange = vi.fn();

    render(
      <MultiSelectFilter
        label="职称"
        allLabel="全部职称"
        selectedValues={["教授", "副教授"]}
        options={["教授", "副教授", "助理教授"]}
        onChange={onChange}
      />,
    );

    expect(screen.getByRole("button", { name: "职称：教授 等 2 项" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "职称：教授 等 2 项" }));
    fireEvent.click(screen.getByRole("button", { name: "清除职称筛选" }));
    fireEvent.click(screen.getByRole("button", { name: "应用" }));

    expect(onChange).toHaveBeenCalledWith([]);
  });
});
