import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";

describe("ConfirmDialog", () => {
  it("renders the dialog through a portal on document.body", () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();

    const { container } = render(
      <div className="translate-y-10">
        <ConfirmDialog
          open
          title="发现新版本"
          description="当前版本 v0.1.0，发现新版本 v2.0.2。是否立即下载并安装？"
          onCancel={onCancel}
          onConfirm={onConfirm}
        />
      </div>,
    );

    expect(container).not.toHaveTextContent("发现新版本");
    expect(screen.getByRole("heading", { name: "发现新版本" })).toBeInTheDocument();
    expect(document.body).toContainElement(screen.getByText("当前版本 v0.1.0，发现新版本 v2.0.2。是否立即下载并安装？"));
  });

  it("closes when clicking the backdrop directly", () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();

    render(
      <ConfirmDialog
        open
        title="Confirm action"
        description="Click outside to close."
        onCancel={onCancel}
        onConfirm={onConfirm}
      />,
    );

    const backdrop = document.body.querySelector(".fixed.inset-0");
    expect(backdrop).toBeInstanceOf(HTMLElement);

    fireEvent.click(backdrop as HTMLElement);

    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("keeps open when a drag starts inside the dialog and ends on the backdrop", () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();

    render(
      <ConfirmDialog
        open
        title="Copy content"
        description="Selectable dialog content."
        onCancel={onCancel}
        onConfirm={onConfirm}
      />,
    );

    const backdrop = document.body.querySelector(".fixed.inset-0");
    expect(backdrop).toBeInstanceOf(HTMLElement);

    fireEvent.mouseDown(screen.getByText("Selectable dialog content."));
    fireEvent.click(backdrop as HTMLElement);

    expect(onCancel).not.toHaveBeenCalled();
  });

  it("closes on the next direct backdrop click after an interrupted drag", () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();

    render(
      <ConfirmDialog
        open
        title="Copy content"
        description="Selectable dialog content."
        onCancel={onCancel}
        onConfirm={onConfirm}
      />,
    );

    const backdrop = document.body.querySelector(".fixed.inset-0");
    expect(backdrop).toBeInstanceOf(HTMLElement);

    fireEvent.mouseDown(screen.getByText("Selectable dialog content."));
    fireEvent.mouseDown(backdrop as HTMLElement);
    fireEvent.click(backdrop as HTMLElement);

    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
