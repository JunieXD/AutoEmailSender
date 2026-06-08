import { fireEvent, render, screen, within } from "@testing-library/react";
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

  it("shows add tag button when professor has no tags", () => {
    const handleAddTag = vi.fn();

    render(
      <DashboardProfessorRow
        professor={{ ...professor, tags: [] }}
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
        onAddTag={handleAddTag}
      />,
    );

    expect(screen.queryByText("暂无标签")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "给导师添加标签" }));

    expect(handleAddTag).toHaveBeenCalled();
  });

  it("notifies when homepage tag order changes", () => {
    const handleTagOrderChange = vi.fn();

    render(
      <DashboardProfessorRow
        professor={{
          ...professor,
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
        }}
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
        onTagOrderChange={handleTagOrderChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "查看全部标签，剩余 1 个" }));
    const dialog = screen.getByRole("dialog", { name: "折叠标签" });
    fireEvent.click(within(dialog).getByRole("button", { name: "选择标签 高强度" }));

    expect(handleTagOrderChange).toHaveBeenCalledWith([3, 1, 2]);
  });
});
