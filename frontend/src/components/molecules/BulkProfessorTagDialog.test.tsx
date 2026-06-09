import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BulkProfessorTagDialog } from "./BulkProfessorTagDialog";
import type { ProfessorTagDTO } from "@/types";

const tags: ProfessorTagDTO[] = [
  {
    id: 1,
    name: "高意愿",
    text_color: "#166534",
    background_color: "#dcfce7",
  },
  {
    id: 2,
    name: "已联系",
    text_color: "#1d4ed8",
    background_color: "#dbeafe",
  },
];

const renderDialog = ({
  onSave = vi.fn(),
  onDeleteTag = vi.fn(),
}: {
  onSave?: ReturnType<typeof vi.fn>;
  onDeleteTag?: ReturnType<typeof vi.fn>;
} = {}) =>
  render(
    <BulkProfessorTagDialog
      open
      selectedCount={3}
      tags={tags}
      saving={false}
      creating={false}
      onCreateTag={vi.fn()}
      onDeleteTag={onDeleteTag}
      onSave={onSave}
      onClose={vi.fn()}
    />,
  );

describe("BulkProfessorTagDialog", () => {
  it("defaults to add mode and submits selected tags", async () => {
    const onSave = vi.fn();
    renderDialog({ onSave });

    expect(
      screen.getByRole("button", { name: "切换为追加标签" }),
    ).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "选择标签 高意愿" }));
    fireEvent.click(screen.getByRole("button", { name: "追加标签" }));

    expect(onSave).toHaveBeenCalledWith({ mode: "add", tagIds: [1] });
  });

  it("submits remove mode", async () => {
    const onSave = vi.fn();
    renderDialog({ onSave });

    fireEvent.click(screen.getByRole("button", { name: "切换为移除标签" }));
    fireEvent.click(screen.getByRole("button", { name: "选择标签 已联系" }));
    fireEvent.click(screen.getByRole("button", { name: "移除标签" }));

    expect(onSave).toHaveBeenCalledWith({ mode: "remove", tagIds: [2] });
  });

  it("allows replace mode with empty tags", async () => {
    const onSave = vi.fn();
    renderDialog({ onSave });

    fireEvent.click(screen.getByRole("button", { name: "切换为覆盖标签" }));
    fireEvent.click(screen.getByRole("button", { name: "覆盖标签" }));

    expect(onSave).toHaveBeenCalledWith({ mode: "replace", tagIds: [] });
  });

  it("disables add and remove save without tags", async () => {
    renderDialog();

    expect(screen.getByRole("button", { name: "追加标签" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "切换为移除标签" }));
    expect(screen.getByRole("button", { name: "移除标签" })).toBeDisabled();
  });

  it("calls delete tag callback from each tag row", () => {
    const onDeleteTag = vi.fn();
    renderDialog({ onDeleteTag });

    fireEvent.click(screen.getByRole("button", { name: "删除标签 高意愿" }));

    expect(onDeleteTag).toHaveBeenCalledWith(tags[0]);
  });
});
