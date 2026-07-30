import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QqGroupButton } from "@/components/molecules/QqGroupButton";

describe("QqGroupButton", () => {
  it("shows the QQ group details and QR code when activated", () => {
    render(<QqGroupButton />);

    const button = screen.getByRole("button", { name: "加入 QQ 群" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    const logo = button.querySelector('[data-qq-logo="outline"]');
    expect(logo?.querySelector("image")).not.toBeInTheDocument();
    expect(logo?.querySelector("feMorphology")).not.toBeInTheDocument();
    expect(
      logo?.querySelector('[data-qq-outer-shape="official-vector"]'),
    ).toBeInTheDocument();
    const roundedOutline = logo?.querySelector('[data-qq-outline-stroke="rounded"]');
    expect(roundedOutline).toHaveAttribute("stroke-linecap", "round");
    expect(roundedOutline).toHaveAttribute("stroke-linejoin", "round");
    const seamlessCutout = logo?.querySelector(
      '[data-qq-outline-cutout="seamless"]',
    );
    expect(seamlessCutout).toHaveAttribute("stroke", "black");
    expect(seamlessCutout).toHaveAttribute("stroke-width", "1");
    expect(logo).toHaveAttribute("viewBox", "-6 2 31 31");
    expect(logo).toHaveClass("h-[19px]", "w-[19px]");
    expect(logo?.getAttribute("class")).not.toContain("translate");

    fireEvent.click(button);

    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("dialog", { name: "QQ 交流群" })).toBeInTheDocument();
    expect(screen.getByTestId("qq-group-popover").style.maxHeight).toBe("440px");
    expect(screen.getByText("遇到问题欢迎在群内反馈。")).toBeInTheDocument();
    expect(screen.getByText("952383261")).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: "QQ群 952383261 二维码" }),
    ).toBeInTheDocument();
  });

  it("closes on Escape and restores focus to the trigger", () => {
    render(<QqGroupButton />);

    const button = screen.getByRole("button", { name: "加入 QQ 群" });
    fireEvent.click(button);
    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.queryByRole("dialog", { name: "QQ 交流群" })).not.toBeInTheDocument();
    expect(button).toHaveFocus();
  });

  it("closes when clicking outside the floating panel", () => {
    render(<QqGroupButton />);

    fireEvent.click(screen.getByRole("button", { name: "加入 QQ 群" }));
    fireEvent.mouseDown(document.body);

    expect(screen.queryByRole("dialog", { name: "QQ 交流群" })).not.toBeInTheDocument();
  });
});
