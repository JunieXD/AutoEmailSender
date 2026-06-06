import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ManagementProfessorRow } from "./ManagementProfessorRow";
import type { ProfessorManagementItemDTO } from "@/types";

const professor: ProfessorManagementItemDTO = {
  id: 1,
  name: "李伟",
  email: "li@example.edu",
  title: "教授",
  university: "示例大学",
  school: "计算机学院",
  department: "人工智能系",
  research_direction: "大语言模型",
  recent_papers: [],
  profile_url: null,
  source_url: null,
  crawl_status: "accepted",
  skip_reason: null,
  archived_at: null,
  created_at: "2026-06-06T08:00:00Z",
  updated_at: "2026-06-06T08:00:00Z",
  tags: [
    {
      id: 1,
      name: "高意愿",
      text_color: "#166534",
      background_color: "#dcfce7",
    },
    {
      id: 2,
      name: "羊导",
      text_color: "#7c2d12",
      background_color: "#ffedd5",
    },
    {
      id: 3,
      name: "高强度",
      text_color: "#991b1b",
      background_color: "#fee2e2",
    },
  ],
};

describe("ManagementProfessorRow", () => {
  it("shows only primary tag and overflow count in the name cell", () => {
    render(
      <ManagementProfessorRow
        professor={professor}
        checked={false}
        selectable
        tableColumns="lg:grid-cols-8"
        onToggleSelection={vi.fn()}
        onEdit={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
      />,
    );

    expect(screen.getByText("高意愿")).toBeInTheDocument();
    expect(screen.queryByText("羊导")).not.toBeInTheDocument();
    expect(screen.getByText("+2")).toBeInTheDocument();
  });

  it("notifies when a popover tag is selected as primary", () => {
    const handlePrimaryTagSelect = vi.fn();

    render(
      <ManagementProfessorRow
        professor={professor}
        checked={false}
        selectable
        tableColumns="lg:grid-cols-8"
        onToggleSelection={vi.fn()}
        onEdit={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onPrimaryTagSelect={handlePrimaryTagSelect}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "查看全部标签，剩余 2 个" }));
    const dialog = screen.getByRole("dialog", { name: "折叠标签" });
    fireEvent.click(within(dialog).getByRole("button", { name: "选择标签 羊导" }));

    expect(handlePrimaryTagSelect).toHaveBeenCalledWith(2);
  });

  it("shows tags and add button in the same name line", () => {
    const handleAddTag = vi.fn();

    render(
      <ManagementProfessorRow
        professor={professor}
        checked={false}
        selectable
        tableColumns="lg:grid-cols-8"
        onToggleSelection={vi.fn()}
        onEdit={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onAddTag={handleAddTag}
      />,
    );

    const nameLine = screen.getByTestId("management-professor-name-line");
    expect(nameLine).toHaveTextContent("李伟");
    expect(nameLine).toHaveTextContent("高意愿");
    expect(nameLine).toHaveTextContent("+2");

    fireEvent.click(within(nameLine).getByRole("button", { name: "给导师添加标签" }));

    expect(handleAddTag).toHaveBeenCalled();
  });
});
