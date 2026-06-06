import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProfessorTagSelector } from "./ProfessorTagSelector";

const tags = [
  { id: 1, name: "高意愿", text_color: "#166534", background_color: "#dcfce7" },
  { id: 2, name: "羊导", text_color: "#7f1d1d", background_color: "#fee2e2" },
];

describe("ProfessorTagSelector", () => {
  it("toggles multiple tags", () => {
    const onChange = vi.fn();

    render(
      <ProfessorTagSelector
        tags={tags}
        selectedTagIds={[1]}
        onChange={onChange}
        onCreateTag={vi.fn()}
        onDeleteTag={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "选择标签 羊导" }));

    expect(onChange).toHaveBeenCalledWith([1, 2]);
  });

  it("submits custom tag with colors", () => {
    const onCreateTag = vi.fn();

    render(
      <ProfessorTagSelector
        tags={tags}
        selectedTagIds={[]}
        onChange={vi.fn()}
        onCreateTag={onCreateTag}
        onDeleteTag={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "+ 自定义标签" }));
    fireEvent.change(screen.getByLabelText("标签名"), {
      target: { value: "已联系" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建标签" }));

    expect(onCreateTag).toHaveBeenCalledWith({
      name: "已联系",
      text_color: "#166534",
      background_color: "#dcfce7",
    });
  });

  it("shows delete buttons only in delete mode", () => {
    render(
      <ProfessorTagSelector
        tags={tags}
        selectedTagIds={[1]}
        onChange={vi.fn()}
        onCreateTag={vi.fn()}
        onDeleteTag={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: "删除标签 高意愿" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "删除标签" }));

    expect(screen.getByRole("button", { name: "删除标签 高意愿" })).toBeInTheDocument();
  });

  it("moves selected tags in professor-specific order by dragging chips", () => {
    const onChange = vi.fn();

    render(
      <ProfessorTagSelector
        tags={tags}
        selectedTagIds={[1, 2]}
        onChange={onChange}
        onCreateTag={vi.fn()}
        onDeleteTag={vi.fn()}
      />,
    );

    fireEvent.dragStart(screen.getByRole("button", { name: "选择标签 羊导" }));
    fireEvent.dragOver(screen.getByRole("button", { name: "选择标签 高意愿" }));
    fireEvent.drop(screen.getByRole("button", { name: "选择标签 高意愿" }));

    expect(onChange).toHaveBeenCalledWith([2, 1]);
  });

  it("renders selected tags in professor-specific order", () => {
    render(
      <ProfessorTagSelector
        tags={tags}
        selectedTagIds={[2, 1]}
        onChange={vi.fn()}
        onCreateTag={vi.fn()}
        onDeleteTag={vi.fn()}
      />,
    );

    expect(
      screen
        .getAllByRole("button", { name: /^选择标签/ })
        .map((button) => button.getAttribute("aria-label")),
    ).toEqual(["选择标签 羊导", "选择标签 高意愿"]);
  });

  it("does not render a separate selected tag order module", () => {
    render(
      <ProfessorTagSelector
        tags={tags}
        selectedTagIds={[1, 2]}
        onChange={vi.fn()}
        onCreateTag={vi.fn()}
        onDeleteTag={vi.fn()}
      />,
    );

    expect(screen.queryByText("已选标签顺序")).not.toBeInTheDocument();
  });

  it("reorders selected tags by dragging tag chips directly", () => {
    const onChange = vi.fn();

    render(
      <ProfessorTagSelector
        tags={tags}
        selectedTagIds={[1, 2]}
        onChange={onChange}
        onCreateTag={vi.fn()}
        onDeleteTag={vi.fn()}
      />,
    );

    fireEvent.dragStart(screen.getByRole("button", { name: "选择标签 羊导" }));
    fireEvent.dragOver(screen.getByRole("button", { name: "选择标签 高意愿" }));
    fireEvent.drop(screen.getByRole("button", { name: "选择标签 高意愿" }));

    expect(onChange).toHaveBeenCalledWith([2, 1]);
  });
});
