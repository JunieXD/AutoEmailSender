import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProfessorTagChips } from "./ProfessorTagChips";

const tag = (id: number, name: string) => ({
  id,
  name,
  text_color: "#166534",
  background_color: "#dcfce7",
});

describe("ProfessorTagChips", () => {
  it("shows no tag state", () => {
    render(<ProfessorTagChips tags={[]} />);

    expect(screen.getByText("暂无标签")).toBeInTheDocument();
  });

  it("limits visible tags and shows overflow count", () => {
    render(
      <ProfessorTagChips
        maxVisible={2}
        tags={[tag(1, "高意愿"), tag(2, "高强度"), tag(3, "羊导")]}
      />,
    );

    expect(screen.getByText("高意愿")).toBeInTheDocument();
    expect(screen.getByText("高强度")).toBeInTheDocument();
    expect(screen.queryByText("羊导")).not.toBeInTheDocument();
    expect(screen.getByText("+1")).toBeInTheDocument();
  });

  it("shows hidden tags in a popover", () => {
    render(
      <ProfessorTagChips
        maxVisible={1}
        tags={[tag(1, "高意愿"), tag(2, "羊导"), tag(3, "高强度")]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "查看全部标签，剩余 2 个" }));
    const dialog = screen.getByRole("dialog", { name: "全部标签" });

    expect(within(dialog).getByText("羊导")).toBeInTheDocument();
    expect(within(dialog).getByText("高强度")).toBeInTheDocument();
  });

  it("calls onTagClick when selecting a popover tag", () => {
    const handleTagClick = vi.fn();

    render(
      <ProfessorTagChips
        maxVisible={1}
        tags={[tag(1, "高意愿"), tag(2, "羊导")]}
        onTagClick={handleTagClick}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "查看全部标签，剩余 1 个" }));
    const dialog = screen.getByRole("dialog", { name: "全部标签" });
    fireEvent.click(within(dialog).getByRole("button", { name: "选择标签 羊导" }));

    expect(handleTagClick).toHaveBeenCalledWith(2);
  });
});
