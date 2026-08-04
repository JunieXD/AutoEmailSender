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
  personal_note: null,
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
  it("shows match scores as plain 0-to-100 numbers", () => {
    render(
      <DashboardProfessorRow
        professor={{ ...professor, match_score: 86 }}
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

    expect(screen.getByText("匹配度 86")).toBeInTheDocument();
    expect(screen.queryByText("匹配度 86%")).not.toBeInTheDocument();
  });

  it("labels an uncalculated score as match degree", () => {
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

    expect(screen.getByText("匹配度 未计算")).toBeInTheDocument();
    expect(screen.queryByText("匹配 未计算")).not.toBeInTheDocument();
  });

  it("shows an active schedule alongside the relationship status", () => {
    render(
      <DashboardProfessorRow
        professor={{ ...professor, status: "replied", has_active_schedule: true }}
        selected={false}
        bulkDisabled={false}
        scoring={false}
        canCalculateMatch
        statusLabel="已回复"
        timeHighlight={null}
        timeLabel={null}
        onToggleSelection={vi.fn()}
        onCalculateMatch={vi.fn()}
        onOpenWorkspace={vi.fn()}
      />,
    );

    expect(screen.getByText("已排程")).toBeInTheDocument();
    expect(screen.getByText("已回复")).toBeInTheDocument();
  });

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

  it("renders homepage tags as non-draggable display chips", () => {
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

    expect(screen.getByText("高意愿")).not.toHaveAttribute("draggable", "true");
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
    expect(screen.queryByText("无")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "给导师添加标签" }));

    expect(handleAddTag).toHaveBeenCalled();
  });

  it("shows the personal note button in the name line and edits it", () => {
    const handleEditNote = vi.fn();

    render(
      <DashboardProfessorRow
        professor={{ ...professor, personal_note: "面聊时提过偏应用方向" }}
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
        onEditNote={handleEditNote}
      />,
    );

    const nameLine = screen.getByTestId("dashboard-professor-name-line");
    const noteButton = within(nameLine).getByRole("button", {
      name: "编辑张明远的个人备注",
    });
    fireEvent.click(noteButton);

    expect(handleEditNote).toHaveBeenCalledTimes(1);
  });

  it("renders homepage overflow tags as non-draggable display chips", () => {
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
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "查看全部标签，剩余 1 个" }));
    const dialog = screen.getByRole("dialog", { name: "折叠标签" });
    expect(within(dialog).getByText("高强度")).not.toHaveAttribute(
      "draggable",
      "true",
    );
  });

  it("hides homepage summary and research direction lines when they are entirely empty", () => {
    render(
      <DashboardProfessorRow
        professor={{
          ...professor,
          title: "   ",
          university: null,
          school: "",
          research_direction: " \t ",
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
      />,
    );

    const row = screen.getByTestId("dashboard-professor-row-1");
    expect(within(row).queryByText("无")).not.toBeInTheDocument();
    expect(
      row.querySelector(".mt-1.text-sm.text-stone-500"),
    ).not.toBeInTheDocument();
    expect(
      row.querySelector("p.mt-2.line-clamp-2.text-sm.leading-6.text-stone-600"),
    ).not.toBeInTheDocument();
  });

  it("keeps only existing homepage summary values when fields are partially empty", () => {
    render(
      <DashboardProfessorRow
        professor={{
          ...professor,
          title: "   ",
          university: "  示例大学  ",
          school: null,
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
      />,
    );

    expect(screen.getByText("示例大学")).toBeInTheDocument();
    expect(screen.queryByText("无")).not.toBeInTheDocument();
  });
});
