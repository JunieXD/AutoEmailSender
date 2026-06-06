import { render, screen } from "@testing-library/react";
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
  ],
};

describe("ManagementProfessorRow", () => {
  it("shows professor tags in the name cell", () => {
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
  });
});
