import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProfessorNoteButton } from "./ProfessorNoteButton";

describe("ProfessorNoteButton", () => {
  it("renders nothing when the note is missing or blank", () => {
    const { rerender } = render(
      <ProfessorNoteButton
        professorName="张明远"
        personalNote={null}
        onEdit={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole("button", { name: "编辑张明远的个人备注" }),
    ).not.toBeInTheDocument();

    rerender(
      <ProfessorNoteButton
        professorName="张明远"
        personalNote={"  \n  "}
        onEdit={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole("button", { name: "编辑张明远的个人备注" }),
    ).not.toBeInTheDocument();
  });

  it("shows a note button with the professor-specific edit label", () => {
    render(
      <ProfessorNoteButton
        professorName="张明远"
        personalNote="面聊时提过偏应用方向"
        onEdit={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("button", { name: "编辑张明远的个人备注" }),
    ).toBeInTheDocument();
  });

  it("shows the full note preview on hover and focus", () => {
    render(
      <ProfessorNoteButton
        professorName="张明远"
        personalNote={"第一行备注\n第二行备注"}
        onEdit={vi.fn()}
      />,
    );

    const button = screen.getByRole("button", {
      name: "编辑张明远的个人备注",
    });
    fireEvent.mouseEnter(button);

    const hoverPreview = screen.getByRole("dialog", {
      name: "张明远的个人备注",
    });
    expect(hoverPreview.textContent).toBe("第一行备注\n第二行备注");
    expect(hoverPreview).toHaveClass("whitespace-pre-wrap");

    fireEvent.mouseLeave(button);
    expect(
      screen.queryByRole("dialog", { name: "张明远的个人备注" }),
    ).not.toBeInTheDocument();

    fireEvent.focus(button);
    expect(
      screen.getByRole("dialog", { name: "张明远的个人备注" }).textContent,
    ).toBe("第一行备注\n第二行备注");
  });

  it("calls onEdit when clicked", () => {
    const handleEdit = vi.fn();
    render(
      <ProfessorNoteButton
        professorName="张明远"
        personalNote="面聊时提过偏应用方向"
        onEdit={handleEdit}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "编辑张明远的个人备注" }));

    expect(handleEdit).toHaveBeenCalledTimes(1);
  });
});
