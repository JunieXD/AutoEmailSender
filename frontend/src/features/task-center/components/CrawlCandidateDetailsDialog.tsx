import {
  MODAL_BACKDROP_CLASS_NAME,
  MODAL_SURFACE_CLASS_NAME,
} from "@/components/atoms/modalStyles";
import { getCandidateEnrichmentFailureMessage } from "@/features/crawl-review/client/crawlJobEvents";
import {
  CRAWL_CANDIDATE_EDIT_INPUT_CLASS,
  CRAWL_CANDIDATE_REVIEW_STATUS_LABELS,
  type CrawlCandidateEditForm,
} from "@/features/task-center/model/crawlCandidateReview";
import type { DismissableLayerClickHandlers } from "@/lib/useDismissableLayerClick";
import { type CrawlCandidateDTO, type CrawlJobEventDTO } from "@/types";
import { Loader2, Pencil, Save, X } from "lucide-react";
import type { JSX } from "react";
import { type FormEvent } from "react";

type Props = {
  selectedCandidateDetail: CrawlCandidateDTO | null;
  candidateDetailLayer: DismissableLayerClickHandlers;
  candidateEditForm: CrawlCandidateEditForm | null;
  selectedCrawlJobCanReview: boolean;
  handleStartCandidateEdit: () => void;
  candidateUpdateLoading: boolean;
  closeSelectedCandidateDetail: () => void;
  handleSaveCandidateEdit: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  handleCandidateEditFieldChange: (
    field: keyof CrawlCandidateEditForm,
    value: string,
  ) => void;
  handleCancelCandidateEdit: () => void;
  renderCandidateExternalUrl: (url: string | null) => JSX.Element | "暂无";
  crawlJobEvents: CrawlJobEventDTO[];
};

export function CrawlCandidateDetailsDialog({
  selectedCandidateDetail,
  candidateDetailLayer,
  candidateEditForm,
  selectedCrawlJobCanReview,
  handleStartCandidateEdit,
  candidateUpdateLoading,
  closeSelectedCandidateDetail,
  handleSaveCandidateEdit,
  handleCandidateEditFieldChange,
  handleCancelCandidateEdit,
  renderCandidateExternalUrl,
  crawlJobEvents,
}: Props) {
  return selectedCandidateDetail ? (
    <div
      className={`${MODAL_BACKDROP_CLASS_NAME} z-[60]`}
      onClick={candidateDetailLayer.onBackdropClick}
      onMouseDown={candidateDetailLayer.onBackdropMouseDown}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label="候选导师详情"
        className={`${MODAL_SURFACE_CLASS_NAME} flex max-h-[90vh] w-full max-w-3xl flex-col`}
        onClick={candidateDetailLayer.onContentClick}
        onMouseDown={candidateDetailLayer.onContentMouseDown}
      >
        <div className="flex items-start justify-between gap-4 border-b border-stone-200 px-6 py-5">
          <div className="min-w-0">
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-stone-400">
              {candidateEditForm ? "编辑候选导师" : "候选导师详情"}
            </p>
            <h3 className="mt-2 text-xl font-semibold text-stone-900">
              {selectedCandidateDetail.name}
            </h3>
            <p className="mt-1 text-sm text-stone-500">
              {candidateEditForm
                ? "手动修正待审核资料，保存后仍可继续补全缺失信息。"
                : selectedCandidateDetail.email?.trim() ||
                  "暂无邮箱（可尝试进行补全）"}
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap justify-end gap-2">
            {!candidateEditForm &&
            selectedCrawlJobCanReview &&
            selectedCandidateDetail.review_status === "pending" ? (
              <button
                type="button"
                onClick={handleStartCandidateEdit}
                disabled={candidateUpdateLoading}
                className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
              >
                <Pencil className="h-4 w-4" />
                编辑
              </button>
            ) : null}
            <button
              type="button"
              onClick={closeSelectedCandidateDetail}
              disabled={candidateUpdateLoading}
              className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
              aria-label="关闭候选导师详情"
            >
              <X className="h-4 w-4" />
              关闭
            </button>
          </div>
        </div>
        {candidateEditForm ? (
          <form
            onSubmit={(event) => void handleSaveCandidateEdit(event)}
            className="flex min-h-0 flex-1 flex-col"
          >
            <div
              data-testid="candidate-detail-scroll"
              className="grid flex-1 gap-4 overflow-y-auto overscroll-contain px-6 py-5 md:grid-cols-2"
            >
              <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500">
                姓名
                <input
                  type="text"
                  required
                  value={candidateEditForm.name}
                  onChange={(event) =>
                    handleCandidateEditFieldChange("name", event.target.value)
                  }
                  className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                />
              </label>
              <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500">
                邮箱
                <input
                  type="email"
                  value={candidateEditForm.email}
                  placeholder="例如 professor@example.edu"
                  onChange={(event) =>
                    handleCandidateEditFieldChange("email", event.target.value)
                  }
                  className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                />
              </label>
              <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500">
                职称
                <input
                  type="text"
                  value={candidateEditForm.title}
                  onChange={(event) =>
                    handleCandidateEditFieldChange("title", event.target.value)
                  }
                  className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                />
              </label>
              <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500">
                部门
                <input
                  type="text"
                  value={candidateEditForm.department}
                  onChange={(event) =>
                    handleCandidateEditFieldChange(
                      "department",
                      event.target.value,
                    )
                  }
                  className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                />
              </label>
              <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500">
                院校
                <input
                  type="text"
                  value={candidateEditForm.university}
                  onChange={(event) =>
                    handleCandidateEditFieldChange(
                      "university",
                      event.target.value,
                    )
                  }
                  className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                />
              </label>
              <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500">
                学院
                <input
                  type="text"
                  value={candidateEditForm.school}
                  onChange={(event) =>
                    handleCandidateEditFieldChange("school", event.target.value)
                  }
                  className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                />
              </label>
              <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500 md:col-span-2">
                研究方向
                <textarea
                  value={candidateEditForm.researchDirection}
                  rows={3}
                  onChange={(event) =>
                    handleCandidateEditFieldChange(
                      "researchDirection",
                      event.target.value,
                    )
                  }
                  className={`${CRAWL_CANDIDATE_EDIT_INPUT_CLASS} resize-y leading-6`}
                />
              </label>
              <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500 md:col-span-2">
                近期论文
                <textarea
                  value={candidateEditForm.recentPapers}
                  rows={5}
                  placeholder="每行填写一篇论文"
                  onChange={(event) =>
                    handleCandidateEditFieldChange(
                      "recentPapers",
                      event.target.value,
                    )
                  }
                  className={`${CRAWL_CANDIDATE_EDIT_INPUT_CLASS} resize-y leading-6`}
                />
                <span className="mt-2 block font-normal text-stone-400">
                  每行一篇，空行会在保存时自动忽略。
                </span>
              </label>
              <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500 md:col-span-2">
                资料页
                <input
                  type="url"
                  value={candidateEditForm.profileUrl}
                  placeholder="https://example.edu/profile"
                  onChange={(event) =>
                    handleCandidateEditFieldChange(
                      "profileUrl",
                      event.target.value,
                    )
                  }
                  className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                />
              </label>
              <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500 md:col-span-2">
                来源页
                <input
                  type="url"
                  value={candidateEditForm.sourceUrl}
                  placeholder="https://example.edu/faculty"
                  onChange={(event) =>
                    handleCandidateEditFieldChange(
                      "sourceUrl",
                      event.target.value,
                    )
                  }
                  className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                />
              </label>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-stone-200 bg-stone-50/80 px-6 py-4">
              <p className="max-w-xl text-pretty text-xs leading-5 text-stone-500">
                保存后仍可补全缺失信息；已有内容（包括本次手动修改）不会被覆盖。
              </p>
              <div className="ml-auto flex items-center gap-2">
                <button
                  type="button"
                  onClick={handleCancelCandidateEdit}
                  disabled={candidateUpdateLoading}
                  className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={candidateUpdateLoading}
                  className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {candidateUpdateLoading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Save className="h-4 w-4" />
                  )}
                  {candidateUpdateLoading ? "保存中…" : "保存修改"}
                </button>
              </div>
            </div>
          </form>
        ) : (
          <div
            data-testid="candidate-detail-scroll"
            className="grid flex-1 gap-4 overflow-y-auto overscroll-contain px-6 py-5 md:grid-cols-2"
          >
            <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
              <div className="text-xs font-medium text-stone-500">职称</div>
              <div className="mt-2 text-sm text-stone-900">
                {selectedCandidateDetail.title || "暂无"}
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
              <div className="text-xs font-medium text-stone-500">
                院校 / 学院
              </div>
              <div className="mt-2 text-sm text-stone-900">
                {[
                  selectedCandidateDetail.university,
                  selectedCandidateDetail.school,
                ]
                  .filter(Boolean)
                  .join(" / ") || "暂无"}
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
              <div className="text-xs font-medium text-stone-500">部门</div>
              <div className="mt-2 text-sm text-stone-900">
                {selectedCandidateDetail.department || "暂无"}
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
              <div className="text-xs font-medium text-stone-500">审核状态</div>
              <div className="mt-2 text-sm text-stone-900">
                {
                  CRAWL_CANDIDATE_REVIEW_STATUS_LABELS[
                    selectedCandidateDetail.review_status
                  ]
                }
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 md:col-span-2">
              <div className="text-xs font-medium text-stone-500">研究方向</div>
              <div className="mt-2 text-sm leading-6 text-stone-900">
                {selectedCandidateDetail.research_direction || "暂无"}
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 md:col-span-2">
              <div className="text-xs font-medium text-stone-500">近期论文</div>
              {selectedCandidateDetail.recent_papers.length > 0 ? (
                <ul className="mt-2 space-y-2 text-sm text-stone-900">
                  {selectedCandidateDetail.recent_papers.map((paper) => (
                    <li key={paper} className="rounded-xl bg-white px-3 py-2">
                      {paper}
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="mt-2 text-sm text-stone-900">暂无</div>
              )}
            </div>
            <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 md:col-span-2">
              <div className="text-xs font-medium text-stone-500">链接信息</div>
              <div className="mt-2 space-y-2 text-sm text-stone-900">
                <div>
                  <span className="text-stone-500">资料页：</span>
                  {renderCandidateExternalUrl(
                    selectedCandidateDetail.profile_url,
                  )}
                </div>
                <div>
                  <span className="text-stone-500">来源页：</span>
                  {renderCandidateExternalUrl(
                    selectedCandidateDetail.source_url,
                  )}
                </div>
              </div>
            </div>
            {getCandidateEnrichmentFailureMessage(
              selectedCandidateDetail,
              crawlJobEvents,
            ) ? (
              <div className="rounded-2xl border border-red-200 bg-red-50/70 px-4 py-3 md:col-span-2">
                <div className="text-xs font-medium text-red-700">
                  补全失败原因
                </div>
                <div className="mt-2 text-sm leading-6 text-red-900">
                  {getCandidateEnrichmentFailureMessage(
                    selectedCandidateDetail,
                    crawlJobEvents,
                  )}
                </div>
              </div>
            ) : null}
          </div>
        )}
      </section>
    </div>
  ) : null;
}
