import type { MaterialDeletionImpactDTO } from "@/types";

const formatIds = (label: string, ids: number[]) => {
  const uniqueIds = [...new Set(ids)].sort((left, right) => left - right);
  if (uniqueIds.length === 0) {
    return null;
  }
  const shownIds = uniqueIds.slice(0, 10);
  return `${label}：ID ${shownIds.join("、")}${uniqueIds.length > shownIds.length ? " 等" : ""}`;
};

export const buildMaterialDeletionConfirmationDescription = (
  impact: MaterialDeletionImpactDTO,
) => {
  const effects = impact.summary.effects;
  const affectedEmailTaskIds = [
    ...effects.detached_primary_task_ids,
    ...effects.removed_attachment_task_ids,
    ...effects.removed_rewrite_source_task_ids,
    ...effects.reset_draft_task_ids,
  ];
  return [
    ...impact.warnings,
    formatIds("受影响的身份", effects.cleared_default_identity_ids),
    formatIds("受影响的邮件任务", affectedEmailTaskIds),
    formatIds("受影响的批量任务", effects.detached_batch_task_ids),
    formatIds("受影响的测试写信会话", effects.detached_test_compose_session_ids),
  ]
    .filter((line): line is string => Boolean(line))
    .join("\n");
};
