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
    const dialog = screen.getByRole("dialog", { name: "折叠标签" });

    expect(within(dialog).getByText("羊导")).toBeInTheDocument();
    expect(within(dialog).getByText("高强度")).toBeInTheDocument();
  });

  it("shows only hidden tags in the overflow popover", () => {
    render(
      <ProfessorTagChips
        maxVisible={2}
        tags={[
          tag(1, "高意愿"),
          tag(2, "羊导"),
          tag(3, "高强度"),
          tag(4, "已退休"),
        ]}
      />,
    );

    fireEvent.mouseEnter(screen.getByRole("button", { name: "查看全部标签，剩余 2 个" }));
    const dialog = screen.getByRole("dialog", { name: "折叠标签" });

    expect(within(dialog).queryByText("高意愿")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("羊导")).not.toBeInTheDocument();
    expect(within(dialog).getByText("高强度")).toBeInTheDocument();
    expect(within(dialog).getByText("已退休")).toBeInTheDocument();
  });

  it("keeps clicked overflow popover open after mouse leaves and closes on outside click", () => {
    render(
      <div>
        <ProfessorTagChips
          maxVisible={1}
          tags={[tag(1, "高意愿"), tag(2, "羊导")]}
        />
        <button type="button">外部按钮</button>
      </div>,
    );

    fireEvent.click(screen.getByRole("button", { name: "查看全部标签，剩余 1 个" }));
    fireEvent.mouseLeave(screen.getByTestId("professor-tag-chips"));

    expect(screen.getByRole("dialog", { name: "折叠标签" })).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByRole("button", { name: "外部按钮" }));

    expect(screen.queryByRole("dialog", { name: "折叠标签" })).not.toBeInTheDocument();
  });

  it("closes hover overflow popover when mouse leaves", () => {
    render(
      <ProfessorTagChips
        maxVisible={1}
        tags={[tag(1, "高意愿"), tag(2, "羊导")]}
      />,
    );

    fireEvent.mouseEnter(screen.getByRole("button", { name: "查看全部标签，剩余 1 个" }));
    expect(screen.getByRole("dialog", { name: "折叠标签" })).toBeInTheDocument();

    fireEvent.mouseLeave(screen.getByTestId("professor-tag-chips"));

    expect(screen.queryByRole("dialog", { name: "折叠标签" })).not.toBeInTheDocument();
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
    const dialog = screen.getByRole("dialog", { name: "折叠标签" });
    fireEvent.click(within(dialog).getByRole("button", { name: "选择标签 羊导" }));

    expect(handleTagClick).toHaveBeenCalledWith(2);
  });

  it("shows add tag button for no tag state", () => {
    const handleAddTag = vi.fn();

    render(<ProfessorTagChips tags={[]} onAddTag={handleAddTag} />);

    fireEvent.click(screen.getByRole("button", { name: "给导师添加标签" }));

    expect(handleAddTag).toHaveBeenCalled();
  });
});
