import { describe, expect, it } from "vitest";

import { shouldBootstrapWorkspaceTask } from "./openWorkspaceThread";
import type { WorkspaceTaskSummaryDTO } from "@/types";

const buildTask = (
  overrides: Partial<WorkspaceTaskSummaryDTO> = {},
): WorkspaceTaskSummaryDTO => ({
  id: 1,
  source: "manual",
  batch_task_id: null,
  parent_task_id: null,
  status: "matched",
  cancellation_reason: null,
  can_continue_manually: false,
  can_write_follow_up: false,
  outreach_generation_mode: "template",
  outreach_template_subject: null,
  outreach_template_body_text: null,
  outreach_template_body_html: null,
  rendered_template_subject: null,
  rendered_template_body_text: null,
  rendered_template_body_html: null,
  match_score: null,
  match_reason: null,
  fit_points: [],
  risk_points: [],
  match_keywords: [],
  generated_subject: null,
  generated_content_text: null,
  generated_content_html: null,
  approved_subject: null,
  approved_body_text: null,
  approved_body_html: null,
  primary_material_id: null,
  primary_material: null,
  selected_material_ids: null,
  approved_at: null,
  scheduled_at: null,
  last_send_attempt_at: null,
  sent_at: null,
  last_rfc_message_id: null,
  retry_count: 0,
  last_error: null,
  is_replied: false,
  estimated_prompt_tokens: null,
  estimated_completion_tokens_upper_bound: null,
  estimated_total_tokens_upper_bound: null,
  last_draft_prompt_tokens: null,
  last_draft_completion_tokens: null,
  last_draft_total_tokens: null,
  ...overrides,
});

describe("shouldBootstrapWorkspaceTask", () => {
  it("bootstraps missing and expired scheduled workspace tasks", () => {
    expect(shouldBootstrapWorkspaceTask(null)).toBe(true);
    expect(
      shouldBootstrapWorkspaceTask(buildTask({
        status: "canceled",
        cancellation_reason: "schedule_expired",
      })),
    ).toBe(true);
  });

  it("bootstraps any batch task so the workspace can switch to an independent manual task", () => {
    expect(
      shouldBootstrapWorkspaceTask(buildTask({
        source: "batch",
        batch_task_id: 10,
        status: "send_failed",
      })),
    ).toBe(true);
    expect(
      shouldBootstrapWorkspaceTask(buildTask({
        source: "batch",
        batch_task_id: 10,
        status: "sent",
      })),
    ).toBe(true);
    expect(
      shouldBootstrapWorkspaceTask(buildTask({
        source: "batch",
        batch_task_id: 10,
        status: "reply_detected",
      })),
    ).toBe(true);
  });
});
