import { fireEvent, render, screen } from "@testing-library/react";
import type { CSSProperties } from "react";
import { describe, expect, it, vi } from "vitest";
import { StatisticsSectionNav } from "@/components/molecules/StatisticsSectionNav";

describe("StatisticsSectionNav", () => {
  const items = [
    { id: "mentor", label: "导师" },
    { id: "email", label: "邮件" },
    { id: "token", label: "Token" },
  ];

  it("renders icon nodes by default and reveals labels on hover", () => {
    render(
      <StatisticsSectionNav
        items={items}
        activeSectionId="mentor"
        onSelect={vi.fn()}
      />,
    );

    expect(screen.queryByText("导师")).not.toBeInTheDocument();
    expect(screen.queryByText("邮件")).not.toBeInTheDocument();
    expect(screen.queryByText("Token")).not.toBeInTheDocument();
    expect(screen.getByTestId("section-nav-icon-mentor")).toBeInTheDocument();
    expect(screen.getByTestId("section-nav-icon-email")).toBeInTheDocument();
    expect(screen.getByTestId("section-nav-icon-token")).toBeInTheDocument();
    expect(screen.getByTestId("section-nav-node-email")).toHaveClass("shadow-[0_10px_22px_-18px_rgba(41,37,36,0.55),inset_0_1px_0_rgba(255,255,255,0.96)]");

    fireEvent.mouseEnter(screen.getByRole("button", { name: "邮件" }));

    expect(screen.getByText("邮件")).toBeInTheDocument();
    expect(screen.getByRole("tooltip")).toHaveTextContent("点击跳转");

    fireEvent.mouseLeave(screen.getByRole("button", { name: "邮件" }));

    expect(screen.queryByText("邮件")).not.toBeInTheDocument();
  });

  it("keeps aria-current on the active section button", () => {
    render(
      <StatisticsSectionNav
        items={items}
        activeSectionId="token"
        onSelect={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Token" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: "导师" })).toHaveAttribute("aria-current", "false");
  });

  it("uses a fixed viewport rail on large screens", () => {
    const { container } = render(
      <StatisticsSectionNav
        items={items}
        activeSectionId="mentor"
        onSelect={vi.fn()}
      />,
    );

    expect(screen.getByTestId("statistics-section-nav")).toHaveClass("lg:fixed");
    expect(screen.getByTestId("statistics-section-nav")).toHaveClass("lg:top-[var(--statistics-section-nav-top,10rem)]");
    expect(screen.getByTestId("statistics-section-nav")).toHaveClass("lg:bottom-10");
    expect(screen.getByTestId("section-nav-frame")).toHaveClass("backdrop-blur-xl");
    expect(screen.getByTestId("section-nav-frame")).toHaveClass("ring-1");
    expect(screen.getByTestId("section-nav-frame")).toHaveClass("bg-[linear-gradient(180deg,rgba(255,255,255,0.82),rgba(250,250,249,0.56))]");
    expect(container.querySelector('[class*="lg:inset-y-5"]')).not.toBeInTheDocument();
  });

  it("accepts a dynamic top offset through a CSS variable", () => {
    render(
      <StatisticsSectionNav
        items={items}
        activeSectionId="mentor"
        onSelect={vi.fn()}
        style={{ "--statistics-section-nav-top": "188px" } as CSSProperties}
      />,
    );

    expect(screen.getByTestId("statistics-section-nav")).toHaveStyle({
      "--statistics-section-nav-top": "188px",
    });
  });

  it("shows current section wording in the hover tooltip for the active item", () => {
    render(
      <StatisticsSectionNav
        items={items}
        activeSectionId="mentor"
        onSelect={vi.fn()}
      />,
    );

    fireEvent.mouseEnter(screen.getByRole("button", { name: "导师" }));

    expect(screen.getByRole("tooltip")).toHaveTextContent("当前版块");
  });
});
