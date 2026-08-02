import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PageSizeSelector } from "./PageSizeSelector";

describe("PageSizeSelector", () => {
  it("emits fixed page size selections", () => {
    const onChange = vi.fn();

    render(<PageSizeSelector value={10} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "每页数量" }));
    fireEvent.click(screen.getByRole("option", { name: "50" }));

    expect(onChange).toHaveBeenCalledWith(50);
  });

  it("does not expose five as a fixed page size option", () => {
    render(<PageSizeSelector value={10} onChange={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "每页数量" }));

    expect(screen.queryByRole("option", { name: "5" })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "10" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "20" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "50" })).toBeInTheDocument();
  });

  it("shows and applies a custom page size", () => {
    const onChange = vi.fn();

    render(<PageSizeSelector value={10} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "每页数量" }));
    fireEvent.click(screen.getByRole("option", { name: "自定义" }));
    const input = screen.getByRole("spinbutton", { name: "自定义每页数量" });

    fireEvent.change(input, { target: { value: "35" } });
    fireEvent.click(screen.getByRole("button", { name: "应用" }));

    expect(onChange).toHaveBeenLastCalledWith(35);
  });

  it("shows an explicit error instead of silently clamping custom values", () => {
    const onChange = vi.fn();

    render(<PageSizeSelector value={10} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "每页数量" }));
    fireEvent.click(screen.getByRole("option", { name: "自定义" }));
    const input = screen.getByRole("spinbutton", { name: "自定义每页数量" });

    fireEvent.change(input, { target: { value: "101" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "请输入 1–100 之间的整数",
    );
    expect(input).toHaveAttribute("aria-invalid", "true");
  });

  it("keeps the current page size when custom input is empty", () => {
    const onChange = vi.fn();

    render(<PageSizeSelector value={20} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "每页数量" }));
    fireEvent.click(screen.getByRole("option", { name: "自定义" }));
    const input = screen.getByRole("spinbutton", { name: "自定义每页数量" });

    fireEvent.change(input, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "应用" }));

    expect(onChange).not.toHaveBeenCalled();
    expect(input).toHaveValue(null);
    expect(screen.getByRole("alert")).toHaveTextContent("请输入每页数量");
  });

  it("supports list-specific fixed options", () => {
    render(
      <PageSizeSelector
        value={5}
        options={[5, 10, 20]}
        onChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "每页数量" }));

    expect(screen.getByRole("option", { name: "5" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "50" })).not.toBeInTheDocument();
  });
});
