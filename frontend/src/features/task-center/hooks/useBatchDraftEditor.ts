import { textToEmailHtml } from "@/lib/richEmail";
import {
  normalizeTemplatePlaceholderHtmlForCompare,
  prepareTemplateEditorHtml,
} from "@/lib/templatePlaceholders";
import { type WorkspaceThreadDTO } from "@/types";
import { useRef, useState } from "react";
import {
  deriveBatchReviewText,
  getBatchReviewDraft,
  type RichEmailValue,
} from "../model/taskCenterConfig";
type BatchReviewDraftSnapshot = {
  subject: string;
  bodyHtml: string;
  selectedMaterialIds: number[];
};
type BatchReviewDraftBaseline = {
  itemId: number;
  snapshot: BatchReviewDraftSnapshot;
};
const buildBatchReviewDraftSnapshot = ({
  subject,
  contentText,
  contentHtml,
  selectedMaterialIds,
}: {
  subject: string;
  contentText: string;
  contentHtml: string;
  selectedMaterialIds: number[];
}): BatchReviewDraftSnapshot => {
  const bodyText = deriveBatchReviewText(contentText, contentHtml);
  const displayHtml =
    contentHtml.trim() || (bodyText ? textToEmailHtml(bodyText) : "");

  return {
    subject,
    bodyHtml: displayHtml
      ? normalizeTemplatePlaceholderHtmlForCompare(
          prepareTemplateEditorHtml(displayHtml),
        )
      : "",
    selectedMaterialIds: [...selectedMaterialIds].sort(
      (left, right) => left - right,
    ),
  };
};
const areBatchReviewDraftSnapshotsEqual = (
  left: BatchReviewDraftSnapshot,
  right: BatchReviewDraftSnapshot,
) =>
  left.subject === right.subject &&
  left.bodyHtml === right.bodyHtml &&
  left.selectedMaterialIds.length === right.selectedMaterialIds.length &&
  left.selectedMaterialIds.every(
    (materialId, index) => materialId === right.selectedMaterialIds[index],
  );
export function useBatchDraftEditor() {
  const [batchReviewThread, setBatchReviewThread] =
    useState<WorkspaceThreadDTO | null>(null);
  const [batchReviewSaving, setBatchReviewSaving] = useState(false);
  const [batchReviewSubject, setBatchReviewSubject] = useState("");
  const [batchReviewContentText, setBatchReviewContentText] = useState("");
  const [batchReviewContentHtml, setBatchReviewContentHtml] = useState("");
  const [batchReviewSelectedMaterialIds, setBatchReviewSelectedMaterialIds] =
    useState<number[]>([]);
  const batchReviewDraftBaselineRef = useRef<BatchReviewDraftBaseline | null>(
    null,
  );
  const batchReviewSavingRef = useRef(false);
  const syncBatchDraftReview = (thread: WorkspaceThreadDTO) => {
    const draft = getBatchReviewDraft(thread);
    setBatchReviewThread(thread);
    setBatchReviewSubject(draft.subject);
    setBatchReviewContentText(draft.text);
    setBatchReviewContentHtml(draft.html);
    setBatchReviewSelectedMaterialIds(draft.selectedMaterialIds);
    batchReviewDraftBaselineRef.current =
      thread.current_task.id === null
        ? null
        : {
            itemId: thread.current_task.id,
            snapshot: buildBatchReviewDraftSnapshot({
              subject: draft.subject,
              contentText: draft.text,
              contentHtml: draft.html,
              selectedMaterialIds: draft.selectedMaterialIds,
            }),
          };
  };
  const handleBatchReviewContentChange = (value: RichEmailValue) => {
    setBatchReviewContentHtml(value.html);
    setBatchReviewContentText(value.text);
  };
  const buildBatchReviewPayload = () => ({
    subject: batchReviewSubject.trim() || null,
    body_text:
      batchReviewContentText.trim() ||
      deriveBatchReviewText("", batchReviewContentHtml),
    body_html: batchReviewContentHtml || null,
    selected_material_ids: batchReviewSelectedMaterialIds,
  });
  const batchReviewHasUnsavedChanges = (itemId: number) => {
    const baseline = batchReviewDraftBaselineRef.current;
    return (
      baseline?.itemId !== itemId ||
      !areBatchReviewDraftSnapshotsEqual(
        baseline.snapshot,
        buildBatchReviewDraftSnapshot({
          subject: batchReviewSubject,
          contentText: batchReviewContentText,
          contentHtml: batchReviewContentHtml,
          selectedMaterialIds: batchReviewSelectedMaterialIds,
        }),
      )
    );
  };
  return {
    batchReviewThread,
    setBatchReviewThread,
    batchReviewSaving,
    setBatchReviewSaving,
    batchReviewSubject,
    setBatchReviewSubject,
    batchReviewContentText,
    setBatchReviewContentText,
    batchReviewContentHtml,
    setBatchReviewContentHtml,
    batchReviewSelectedMaterialIds,
    setBatchReviewSelectedMaterialIds,
    batchReviewDraftBaselineRef,
    batchReviewSavingRef,
    syncBatchDraftReview,
    handleBatchReviewContentChange,
    buildBatchReviewPayload,
    batchReviewHasUnsavedChanges,
  };
}
