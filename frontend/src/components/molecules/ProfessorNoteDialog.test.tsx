import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProfessorNoteDialog } from "./ProfessorNoteDialog";

const professor = {
  id: 1,
  name: "张明远",
  university: "示例大学",
  school: "计算机学院",
};

describe("ProfessorNoteDialog", () => {
  it("renders professor context when open", () => {
    render(
      <ProfessorNoteDialog
        open
        professor={professor}
        initialNote="已有备注"
        saving={false}
        onSave={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    const dialog = screen.getByRole("dialog", { name: "编辑个人备注" });
    expect(within(dialog).getByText("张明远")).toBeInTheDocument();
    expect(within(dialog).getByText("示例大学 / 计算机学院")).toBeInTheDocument();
  });

  it("saves the edited note", () => {
    const handleSave = vi.fn();
    render(
      <ProfessorNoteDialog
        open
        professor={professor}
        initialNote="已有备注"
        saving={false}
        onSave={handleSave}
        onClose={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText("个人备注"), {
      target: { value: "新备注" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存备注" }));

    expect(handleSave).toHaveBeenCalledWith("新备注");
  });

  it("renders nothing while closed", () => {
    render(
      <ProfessorNoteDialog
        open={false}
        professor={professor}
        initialNote="已有备注"
        saving={false}
        onSave={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole("dialog", { name: "编辑个人备注" }),
    ).not.toBeInTheDocument();
  });

  it("disables the save button while saving", () => {
    render(
      <ProfessorNoteDialog
        open
        professor={professor}
        initialNote="已有备注"
        saving
        onSave={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "保存备注" })).toBeDisabled();
  });
});
