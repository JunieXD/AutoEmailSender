import { describe, expect, expectTypeOf, it } from "vitest";
import {
  PROFESSOR_DASHBOARD_STATUS_LABELS,
  PROFESSOR_DASHBOARD_STATUS_OPTIONS,
  filterProfessorsByDashboardStatus,
} from "@/features/professor-status/dashboardStatus";
import {
  PROFESSOR_STATUS_LABELS,
  type ProfessorDashboardItemDTO,
  type WorkspaceTaskStatus,
} from "@/types";

const createProfessor = (
  id: number,
  status: ProfessorDashboardItemDTO["status"],
  overrides: Partial<ProfessorDashboardItemDTO> = {},
): ProfessorDashboardItemDTO => ({
  id,
  name: `${status}-导师`,
  email: `${status}@example.edu`,
  title: "Professor",
  university: "示例大学",
  school: "计算学院",
  department: "计算机系",
  research_direction: "多智能体",
  recent_papers: [],
  match_score: null,
  sent_count: 0,
  status,
  ...overrides,
});

describe("professor dashboard status helper", () => {
  it("keeps task labels aligned with the current workspace task states", () => {
    expectTypeOf(PROFESSOR_STATUS_LABELS).toEqualTypeOf<
      Record<WorkspaceTaskStatus, string>
    >();
    expect(PROFESSOR_STATUS_LABELS).toEqual({
      discovered: "待处理",
      matched: "待生成",
      generating_draft: "正在生成草稿",
      draft_failed: "草稿生成失败",
      review_required: "待审核",
      approved: "待发送",
      scheduled: "已排程",
      sending: "发送中",
      sent: "已发送",
      send_failed: "发送失败",
      reply_detected: "已回复",
      canceled: "已取消",
    });
  });

  it("keeps relationship labels and adds the scheduled homepage filter", () => {
    expect(PROFESSOR_DASHBOARD_STATUS_LABELS).toEqual({
      not_contacted: "未开始",
      preparing: "准备中",
      ready_to_send: "待发送",
      contacted: "已联系",
      replied: "已回复",
      failed: "失败",
    });
    expect(PROFESSOR_DASHBOARD_STATUS_LABELS).not.toHaveProperty("needs_attention");
    expect(PROFESSOR_DASHBOARD_STATUS_OPTIONS).toEqual([
      ["not_contacted", "未开始"],
      ["preparing", "准备中"],
      ["ready_to_send", "待发送"],
      ["contacted", "已联系"],
      ["replied", "已回复"],
      ["failed", "失败"],
      ["scheduled", "已排程"],
    ]);
  });

  it("filters relationship status and active schedules independently", () => {
    const professors = [
      createProfessor(1, "not_contacted"),
      createProfessor(2, "preparing"),
      createProfessor(3, "ready_to_send"),
      createProfessor(4, "contacted"),
      createProfessor(5, "replied"),
      createProfessor(6, "failed"),
      createProfessor(7, "replied", { has_active_schedule: true }),
    ];

    expect(filterProfessorsByDashboardStatus(professors, "all")).toEqual(professors);
    expect(filterProfessorsByDashboardStatus(professors, "preparing")).toEqual([
      professors[1],
    ]);
    expect(filterProfessorsByDashboardStatus(professors, "failed")).toEqual([
      professors[5],
    ]);
    expect(filterProfessorsByDashboardStatus(professors, "scheduled")).toEqual([
      professors[6],
    ]);
  });
});
