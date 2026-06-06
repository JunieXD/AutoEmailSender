import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DashboardProfessorRow } from "./DashboardProfessorRow";
import type { ProfessorDashboardItemDTO } from "@/types";

const professor: ProfessorDashboardItemDTO = {
  id: 1,
  name: "张明远",
  email: "zhang@example.edu",
  title: "教授",
  university: "示例大学",
  school: "计算机学院",
  department: "人工智能系",
  research_direction: "大语言模型",
  recent_papers: [],
  match_score: null,
  sent_count: 0,
  status: "not_contacted",
  last_sent_at: null,
  last_replied_at: null,
  tags: [
    {
      id: 1,
      name: "高意愿",
      text_color: "#166534",
      background_color: "#dcfce7",
    },
  ],
};

describe("DashboardProfessorRow", () => {
  it("shows professor tags in the same line as the name", () => {
    render(
      <DashboardProfessorRow
        professor={professor}
        selected={false}
        bulkDisabled={false}
        scoring={false}
        canCalculateMatch
        statusLabel="未发送"
        timeHighlight={null}
        timeLabel={null}
        onToggleSelection={vi.fn()}
        onCalculateMatch={vi.fn()}
        onOpenWorkspace={vi.fn()}
      />,
    );

    const nameLine = screen.getByTestId("dashboard-professor-name-line");
    expect(nameLine).toHaveTextContent("张明远");
    expect(nameLine).toHaveTextContent("高意愿");
  });
});
