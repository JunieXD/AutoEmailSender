import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NativeSelectField } from "@/components/atoms/NativeSelectField";

describe("NativeSelectField", () => {
  it("keeps the content popover below the sticky header layer", () => {
    render(
      <NativeSelectField
        label="学校"
        ariaLabel="学校筛选"
        value=""
        onChange={vi.fn()}
      >
        <option value="">全部学校</option>
        <option value="demo">示例大学</option>
      </NativeSelectField>,
    );

    fireEvent.click(screen.getByLabelText("学校筛选"));

    const menu = screen.getByRole("listbox");
    expect(menu).toHaveClass("absolute");
    expect(menu).toHaveClass("z-40");
    expect(menu).not.toHaveClass("z-50");
  });
});
