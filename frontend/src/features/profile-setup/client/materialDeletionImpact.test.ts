import { describe, expect, it } from "vitest";
import type { MaterialDeletionImpactDTO } from "@/types";
import { buildMaterialDeletionConfirmationDescription } from "./materialDeletionImpact";

const impact: MaterialDeletionImpactDTO = {
  snapshot_version: "1",
  material_id: 7,
  deletion_fingerprint: "fingerprint",
  summary: {
    material: {
      id: 7,
      name: "申请材料",
      source_identity_id: 2,
      identity_id: 2,
      is_primary: true,
      default_for_identity_ids: [2, 3],
    },
    effects: {
      clears_default_reference_material: true,
      cleared_default_identity_ids: [2, 3],
      detached_primary_task_ids: [21],
      removed_attachment_task_ids: [21, 22],
      removed_rewrite_source_task_ids: [23],
      reset_draft_task_ids: [22],
      detached_test_compose_session_ids: [31],
      detached_batch_task_ids: [41],
      detached_match_analysis_run_count: 1,
      detached_match_result_count: 2,
      completed_batch_task_ids: [],
    },
  },
  warnings: ["确认后会永久删除该材料文件，无法从应用内恢复。"],
};

describe("buildMaterialDeletionConfirmationDescription", () => {
  it("names the exact business records affected by a permanent deletion", () => {
    const description = buildMaterialDeletionConfirmationDescription(impact);

    expect(description).toContain("受影响的身份：ID 2、3");
    expect(description).toContain("受影响的邮件任务：ID 21、22、23");
    expect(description).toContain("受影响的批量任务：ID 41");
    expect(description).toContain("受影响的测试写信会话：ID 31");
  });
});
