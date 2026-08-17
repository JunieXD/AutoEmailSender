import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NativeSelectField } from "@/components/atoms/NativeSelectField";

describe("NativeSelectField", () => {
  it("renders an embedded trigger without a second field shell", () => {
    render(
      <div className="h-12">
        <NativeSelectField ariaLabel="排序" value="latest" embedded>
          <option value="latest">最近更新</option>
        </NativeSelectField>
      </div>,
    );

    const trigger = screen.getByRole("button", { name: "排序" });
    expect(trigger).toHaveClass("h-full", "bg-transparent");
    expect(trigger).not.toHaveClass("ui-select-shell");
  });

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
  it("keeps option semantics when rendering custom options", () => {
    render(
      <NativeSelectField
        label="排序"
        ariaLabel="排序"
        value="sent"
        onChange={vi.fn()}
        renderOption={(option, { selectOption }) => (
          <button type="button" onClick={selectOption}>
            {option.label}
          </button>
        )}
      >
        <option value="sent">发送时间</option>
        <option value="replied">回复时间</option>
      </NativeSelectField>,
    );

    fireEvent.click(screen.getByLabelText("排序"));

    expect(
      screen.getByRole("option", { name: "发送时间" }),
    ).toHaveAttribute("aria-selected", "true");
    expect(
      screen.getByRole("option", { name: "回复时间" }),
    ).toHaveAttribute("aria-selected", "false");
  });
});
