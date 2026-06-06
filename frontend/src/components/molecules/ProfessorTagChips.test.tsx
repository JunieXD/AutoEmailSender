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

  it("reorders hidden tags from the overflow popover", () => {
    const handleTagOrderChange = vi.fn();

    render(
      <ProfessorTagChips
        maxVisible={1}
        tags={[
          tag(1, "高意愿"),
          tag(2, "羊导"),
          tag(3, "高强度"),
        ]}
        draggableTags
        onTagOrderChange={handleTagOrderChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "查看全部标签，剩余 2 个" }));
    const dialog = screen.getByRole("dialog", { name: "折叠标签" });
    const hiddenTag = within(dialog).getByText("高强度");
    const targetTag = within(dialog).getByText("羊导");

    fireEvent.dragStart(hiddenTag);
    fireEvent.dragOver(targetTag);
    fireEvent.drop(targetTag);

    expect(handleTagOrderChange).toHaveBeenCalledWith([1, 3, 2]);
  });

  it("does not treat a hidden tag drag as a popover tag click", () => {
    const handleTagClick = vi.fn();
    const handleTagOrderChange = vi.fn();

    render(
      <ProfessorTagChips
        maxVisible={1}
        tags={[
          tag(1, "高意愿"),
          tag(2, "羊导"),
          tag(3, "高强度"),
        ]}
        draggableTags
        onTagClick={handleTagClick}
        onTagOrderChange={handleTagOrderChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "查看全部标签，剩余 2 个" }));
    const dialog = screen.getByRole("dialog", { name: "折叠标签" });
    const hiddenTag = within(dialog).getByRole("button", { name: "选择标签 高强度" });
    const targetTag = within(dialog).getByRole("button", { name: "选择标签 羊导" });

    fireEvent.dragStart(hiddenTag);
    fireEvent.dragOver(targetTag);
    fireEvent.drop(targetTag);
    fireEvent.click(hiddenTag);

    expect(handleTagOrderChange).toHaveBeenCalledWith([1, 3, 2]);
    expect(handleTagClick).not.toHaveBeenCalled();
  });

  it("keeps the next normal hidden tag click after dropping on a visible tag", () => {
    const handleTagClick = vi.fn();
    const handleTagOrderChange = vi.fn();

    render(
      <ProfessorTagChips
        maxVisible={1}
        tags={[
          tag(1, "高意愿"),
          tag(2, "羊导"),
          tag(3, "高强度"),
        ]}
        draggableTags
        onTagClick={handleTagClick}
        onTagOrderChange={handleTagOrderChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "查看全部标签，剩余 2 个" }));
    const dialog = screen.getByRole("dialog", { name: "折叠标签" });
    const hiddenTag = within(dialog).getByRole("button", { name: "选择标签 高强度" });
    const visibleTag = screen.getByText("高意愿");

    fireEvent.dragStart(hiddenTag);
    fireEvent.dragOver(visibleTag);
    fireEvent.drop(visibleTag);
    fireEvent.click(within(dialog).getByRole("button", { name: "选择标签 羊导" }));

    expect(handleTagOrderChange).toHaveBeenCalledWith([3, 1, 2]);
    expect(handleTagClick).toHaveBeenCalledWith(2);
  });

  it("shows add tag button for no tag state", () => {
    const handleAddTag = vi.fn();

    render(<ProfessorTagChips tags={[]} onAddTag={handleAddTag} />);

    fireEvent.click(screen.getByRole("button", { name: "给导师添加标签" }));

    expect(handleAddTag).toHaveBeenCalled();
  });

  it("shows add tag button after existing tags and overflow count", () => {
    const handleAddTag = vi.fn();

    render(
      <ProfessorTagChips
        maxVisible={1}
        tags={[tag(1, "高意愿"), tag(2, "羊导")]}
        onAddTag={handleAddTag}
      />,
    );

    const root = screen.getByTestId("professor-tag-chips");

    expect(root).toHaveTextContent("高意愿+1");
    fireEvent.click(screen.getByRole("button", { name: "给导师添加标签" }));
    expect(handleAddTag).toHaveBeenCalled();
  });
});
