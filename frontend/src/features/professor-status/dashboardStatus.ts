import type {
  ProfessorDashboardFilterStatus,
  ProfessorDashboardItemDTO,
  ProfessorDashboardStatus,
} from "@/types";

export type ProfessorDashboardStatusFilter =
  | "all"
  | ProfessorDashboardFilterStatus;

export const PROFESSOR_DASHBOARD_STATUS_LABELS: Record<ProfessorDashboardStatus, string> = {
  not_contacted: "未开始",
  preparing: "准备中",
  ready_to_send: "待发送",
  contacted: "已联系",
  replied: "已回复",
  failed: "失败",
};

export const PROFESSOR_DASHBOARD_STATUS_OPTIONS: Array<
  [ProfessorDashboardFilterStatus, string]
> = [
  ...(Object.entries(PROFESSOR_DASHBOARD_STATUS_LABELS) as Array<
    [ProfessorDashboardStatus, string]
  >),
  ["scheduled", "已排程"],
];

export const filterProfessorsByDashboardStatus = (
  professors: ProfessorDashboardItemDTO[],
  status: ProfessorDashboardStatusFilter,
): ProfessorDashboardItemDTO[] => {
  if (status === "all") {
    return professors;
  }
  return professors.filter((professor) =>
    status === "scheduled"
      ? Boolean(professor.has_active_schedule)
      : professor.status === status,
  );
};

export const getProfessorDashboardStatusLabel = (
  status: ProfessorDashboardFilterStatus,
): string =>
  status === "scheduled"
    ? "已排程"
    : PROFESSOR_DASHBOARD_STATUS_LABELS[status];
