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
    expect(screen.getByRole("button", { name: "关闭确认弹层" })).toHaveClass(
      "h-9",
      "w-9",
      "shrink-0",
      "rounded-full",
    );
  });

  it("keeps open when a drag starts inside the dialog and ends on the backdrop", () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();

    render(
      <ConfirmDialog
        open
        title="复制内容"
        description="可选择的弹窗内容。"
        onCancel={onCancel}
        onConfirm={onConfirm}
      />,
    );

    const backdrop = document.body.querySelector(".fixed.inset-0");
    expect(backdrop).toBeInstanceOf(HTMLElement);

    fireEvent.mouseDown(screen.getByText("可选择的弹窗内容。"));
    fireEvent.click(backdrop as HTMLElement);

    expect(onCancel).not.toHaveBeenCalled();
  });

  it("keeps actions reachable when the description is long", () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    const longDescription = Array.from(
      { length: 80 },
      (_, index) => `导师 ${index + 1}`,
    ).join("\n");

    render(
      <ConfirmDialog
        open
        title="删除标签“高意愿”？"
        description={longDescription}
        confirmLabel="确认删除"
        onCancel={onCancel}
        onConfirm={onConfirm}
      />,
    );

    const dialog = screen.getByRole("dialog", { name: "删除标签“高意愿”？" });
    const description = screen.getByText(/导师 80/);

    expect(dialog).toHaveClass("max-h-[calc(100vh-2rem)]");
    expect(description).toHaveClass("max-h-[min(42vh,22rem)]", "overflow-y-auto");
    expect(
      screen.getByRole("button", { name: "确认删除" }),
    ).toBeInTheDocument();
  });
});
