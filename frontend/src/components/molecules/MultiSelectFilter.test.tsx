import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MultiSelectFilter } from "@/components/molecules/MultiSelectFilter";

describe("MultiSelectFilter", () => {
  it("keeps the content popover below the sticky header layer", () => {
    render(
      <MultiSelectFilter
        label="学校"
        allLabel="全部学校"
        selectedValues={[]}
        options={["示例大学", "第二大学"]}
        onToggle={vi.fn()}
        onClear={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "学校：全部学校" }));

    const menu = screen.getByRole("listbox").closest(".absolute");
    expect(menu).toHaveClass("z-40");
    expect(menu).not.toHaveClass("z-50");
  });
});
