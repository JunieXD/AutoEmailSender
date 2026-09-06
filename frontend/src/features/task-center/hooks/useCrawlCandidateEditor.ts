import { useNotification } from "@/context/NotificationContext";
import { updateCrawlCandidate } from "@/lib/api/crawlJobsApi";
import { useConfirmDialog } from "@/lib/useConfirmDialog";
import { type CrawlCandidateDTO } from "@/types";
import { useCallback, useState, type FormEvent } from "react";
import {
  hasUnsavedCrawlCandidateChanges,
  toCrawlCandidateEditForm,
  toCrawlCandidateUpdatePayload,
  type CrawlCandidateEditForm,
} from "../model/crawlCandidateReview";

type CandidateEditorOptions = {
  selectedCrawlJobCanReview: boolean;
  setCrawlJobCandidates: React.Dispatch<
    React.SetStateAction<CrawlCandidateDTO[]>
  >;
  confirm: ReturnType<typeof useConfirmDialog>["confirm"];
  notifyError: ReturnType<typeof useNotification>["notifyError"];
  notifySuccess: ReturnType<typeof useNotification>["notifySuccess"];
};

export function useCrawlCandidateEditor({
  selectedCrawlJobCanReview,
  setCrawlJobCandidates,
  confirm,
  notifyError,
  notifySuccess,
}: CandidateEditorOptions) {
  const [selectedCandidateDetail, setSelectedCandidateDetail] =
    useState<CrawlCandidateDTO | null>(null);
  const [candidateEditForm, setCandidateEditForm] =
    useState<CrawlCandidateEditForm | null>(null);
  const [candidateUpdateLoading, setCandidateUpdateLoading] = useState(false);
  const handleStartCandidateEdit = () => {
    if (
      !selectedCandidateDetail ||
      selectedCandidateDetail.review_status !== "pending" ||
      !selectedCrawlJobCanReview
    ) {
      return;
    }
    setCandidateEditForm(toCrawlCandidateEditForm(selectedCandidateDetail));
  };
  const handleCancelCandidateEdit = () => {
    if (candidateUpdateLoading) {
      return;
    }
    setCandidateEditForm(null);
  };
  const handleCandidateEditFieldChange = (
    field: keyof CrawlCandidateEditForm,
    value: string,
  ) => {
    setCandidateEditForm((currentForm) =>
      currentForm ? { ...currentForm, [field]: value } : currentForm,
    );
  };
  const handleSaveCandidateEdit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (
      !selectedCandidateDetail ||
      !candidateEditForm ||
      candidateUpdateLoading
    ) {
      return;
    }
    if (
      selectedCandidateDetail.review_status !== "pending" ||
      !selectedCrawlJobCanReview
    ) {
      notifyError(
        "无法保存导师信息",
        "该候选导师已不在待审核状态，请刷新任务后重试。",
      );
      return;
    }

    const payload = toCrawlCandidateUpdatePayload(
      selectedCandidateDetail,
      candidateEditForm,
    );
    if (!payload.name) {
      notifyError("无法保存导师信息", "导师姓名不能为空。");
      return;
    }

    setCandidateUpdateLoading(true);
    try {
      const updatedCandidate = await updateCrawlCandidate(
        selectedCandidateDetail.id,
        payload,
      );
      setCrawlJobCandidates((currentCandidates) =>
        currentCandidates.map((candidate) =>
          candidate.id === updatedCandidate.id ? updatedCandidate : candidate,
        ),
      );
      setSelectedCandidateDetail(updatedCandidate);
      setCandidateEditForm(null);
      notifySuccess("导师信息已保存", "后续补全仅填写空缺字段。");
    } catch (actionError) {
      const message =
        actionError instanceof Error
          ? actionError.message
          : "保存候选导师信息失败";
      notifyError("保存候选导师信息失败", message);
    } finally {
      setCandidateUpdateLoading(false);
    }
  };
  const requestCloseSelectedCandidateDetail = useCallback(async () => {
    if (candidateUpdateLoading) {
      return;
    }
    if (
      selectedCandidateDetail &&
      candidateEditForm &&
      hasUnsavedCrawlCandidateChanges(
        selectedCandidateDetail,
        candidateEditForm,
      )
    ) {
      const shouldDiscardChanges = await confirm({
        title: "放弃未保存的修改？",
        description: "关闭后，本次对候选导师信息的修改将不会保存。",
        confirmLabel: "不保存并关闭",
        cancelLabel: "继续编辑",
        tone: "danger",
      });
      if (!shouldDiscardChanges) {
        return;
      }
    }
    setCandidateEditForm(null);
    setSelectedCandidateDetail(null);
  }, [
    candidateEditForm,
    candidateUpdateLoading,
    confirm,
    selectedCandidateDetail,
  ]);
  const closeSelectedCandidateDetail = useCallback(() => {
    void requestCloseSelectedCandidateDetail();
  }, [requestCloseSelectedCandidateDetail]);
  return {
    selectedCandidateDetail,
    setSelectedCandidateDetail,
    candidateEditForm,
    setCandidateEditForm,
    candidateUpdateLoading,
    setCandidateUpdateLoading,
    handleStartCandidateEdit,
    handleCancelCandidateEdit,
    handleCandidateEditFieldChange,
    handleSaveCandidateEdit,
    requestCloseSelectedCandidateDetail,
    closeSelectedCandidateDetail,
  };
}
