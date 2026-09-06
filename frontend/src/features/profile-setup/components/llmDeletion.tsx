import {
  MODAL_BACKDROP_CLASS_NAME,
  MODAL_SURFACE_CLASS_NAME,
} from "@/components/atoms/modalStyles";
import { useDismissableLayerClick } from "@/lib/useDismissableLayerClick";
import { useDocumentScrollLock } from "@/lib/useDocumentScrollLock";
import {
  type LLMProfileDeletionImpactDTO,
  type LLMProfileReferenceCountsDTO,
} from "@/types";
import { AlertTriangle, Loader2, Trash2, X } from "lucide-react";
import { useCallback, useEffect } from "react";

const LLM_REFERENCE_LABELS: Array<
  [keyof LLMProfileReferenceCountsDTO, string]
> = [
  ["batch_tasks", "批量活动"],
  ["email_tasks", "邮件任务"],
  ["email_logs", "邮件与通信记录"],
  ["match_analysis_jobs", "匹配分析任务"],
  ["match_analysis_job_items", "匹配分析明细"],
  ["match_analysis_runs", "匹配运行记录"],
  ["match_results", "导师匹配结果"],
  ["test_compose_sessions", "测试写信会话"],
  ["test_compose_messages", "测试邮件记录"],
  ["crawl_jobs", "抓取与信息补全任务"],
  ["crawl_runs", "抓取运行记录"],
  ["crawl_pages", "抓取页面"],
  ["crawl_candidates", "抓取候选数据"],
  ["crawl_token_usages", "抓取 Token 记录"],
  ["agent_change_plans", "Agent 待办与历史计划"],
  ["operation_logs", "操作日志"],
];

export const LLMDeletionDialog = ({
  impact,
  replacementProfiles,
  replacementProfileId,
  busy,
  onReplacementChange,
  onClose,
  onConfirm,
}: {
  impact: LLMProfileDeletionImpactDTO;
  replacementProfiles: Array<{
    id: number;
    name: string;
    model_name: string;
  }>;
  replacementProfileId: number | null;
  busy: boolean;
  onReplacementChange: (profileId: number | null) => void;
  onClose: () => void;
  onConfirm: () => void;
}) => {
  useDocumentScrollLock(true);
  const dismiss = useCallback(() => {
    if (!busy) {
      onClose();
    }
  }, [busy, onClose]);
  const {
    onBackdropClick,
    onBackdropMouseDown,
    onContentClick,
    onContentMouseDown,
  } = useDismissableLayerClick(dismiss);
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [busy, onClose]);

  const references = LLM_REFERENCE_LABELS.filter(
    ([key]) => impact.references[key] > 0,
  );
  const automaticActions = [
    impact.automatic_actions.cancel_email_task_ids.length > 0
      ? `等待生成草稿的邮件任务：ID ${impact.automatic_actions.cancel_email_task_ids.join("、")}`
      : null,
    impact.automatic_actions.cancel_match_analysis_job_ids.length > 0
      ? `匹配分析任务：ID ${impact.automatic_actions.cancel_match_analysis_job_ids.join("、")}`
      : null,
    impact.automatic_actions.cancel_crawl_job_ids.length > 0
      ? `智能抓取或信息补全任务：ID ${impact.automatic_actions.cancel_crawl_job_ids.join("、")}`
      : null,
  ].filter((item): item is string => item !== null);

  return (
    <div
      className={`${MODAL_BACKDROP_CLASS_NAME} z-[90]`}
      onClick={onBackdropClick}
      onMouseDown={onBackdropMouseDown}
    >
      <div
        aria-describedby="llm-deletion-description"
        aria-labelledby="llm-deletion-title"
        aria-modal="true"
        className={`${MODAL_SURFACE_CLASS_NAME} flex max-h-[min(88vh,48rem)] w-full max-w-2xl flex-col`}
        onClick={onContentClick}
        onMouseDown={onContentMouseDown}
        role="dialog"
      >
        <div className="absolute inset-x-0 top-0 h-24 bg-[radial-gradient(circle_at_top,rgba(251,191,36,0.18),transparent_68%)]" />
        <div className="relative flex min-h-0 flex-col px-6 py-6">
          <div className="flex items-start justify-between gap-4">
            <div className="flex min-w-0 items-start gap-3">
              <div className="mt-0.5 flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-red-100 text-red-600 shadow-sm shadow-red-100/80">
                <AlertTriangle aria-hidden="true" className="h-5 w-5" />
              </div>
              <div className="min-w-0 flex-1">
                <h2
                  id="llm-deletion-title"
                  className="text-lg font-semibold tracking-[0.01em] text-stone-900"
                >
                  {impact.can_delete ? "删除模型配置" : "暂时无法删除模型配置"}
                </h2>
                <p
                  id="llm-deletion-description"
                  className="mt-2 text-sm leading-6 text-stone-600"
                >
                  “{impact.profile_name}” · {impact.model_name}
                </p>
              </div>
            </div>
            <button
              type="button"
              aria-label="关闭确认弹层"
              className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-stone-200 bg-white/80 text-stone-500 transition hover:border-stone-300 hover:bg-white hover:text-stone-900 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={busy}
              onClick={dismiss}
            >
              <X aria-hidden="true" className="h-4 w-4" />
            </button>
          </div>

          <div className="relative min-h-0 flex-1 space-y-5 overflow-y-auto pr-1 pt-6">
            {impact.blockers.length > 0 && (
              <section className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3">
                <div className="flex items-center gap-2 text-sm font-semibold text-rose-900">
                  <AlertTriangle aria-hidden="true" className="h-4 w-4" />
                  以下操作结束前无法删除
                </div>
                <ul className="mt-2 space-y-2 text-sm text-rose-800">
                  {impact.blockers.map((blocker) => (
                    <li key={blocker.kind}>
                      {blocker.label}：{blocker.count} 项
                      {blocker.entity_ids.length > 0
                        ? `（ID ${blocker.entity_ids.join("、")}${blocker.count > blocker.entity_ids.length ? " 等" : ""}）`
                        : ""}
                      <span className="mt-0.5 block text-xs text-rose-700">
                        定位：{blocker.surface}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {automaticActions.length > 0 && (
              <section className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3">
                <h3 className="text-sm font-semibold text-amber-950">
                  确认删除后会自动取消
                </h3>
                <ul className="mt-2 space-y-1 text-sm text-amber-900">
                  {automaticActions.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </section>
            )}

            <section className="rounded-2xl border border-stone-200/80 bg-white/70 px-4 py-4">
              <h3 className="text-sm font-semibold text-stone-900">
                保留的历史数据
              </h3>
              {references.length > 0 ? (
                <dl className="mt-3 grid grid-cols-1 gap-x-5 gap-y-2 sm:grid-cols-2">
                  {references.map(([key, label]) => (
                    <div
                      key={key}
                      className="flex items-center justify-between gap-4 border-b border-stone-100 py-1.5 text-sm"
                    >
                      <dt className="text-stone-600">{label}</dt>
                      <dd className="font-medium tabular-nums text-stone-900">
                        {impact.references[key]}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="mt-2 text-sm text-stone-500">
                  没有关联的历史业务数据。
                </p>
              )}
            </section>

            {impact.is_default && impact.can_delete && (
              <section className="rounded-2xl border border-primary/15 bg-primary/5 px-4 py-4">
                <label
                  htmlFor="llm-default-replacement"
                  className="text-sm font-semibold text-stone-900"
                >
                  删除后的默认模型
                </label>
                <select
                  id="llm-default-replacement"
                  className="ui-input mt-2 w-full"
                  disabled={busy}
                  value={replacementProfileId ?? ""}
                  onChange={(event) =>
                    onReplacementChange(
                      event.target.value ? Number(event.target.value) : null,
                    )
                  }
                >
                  <option value="">暂不设置默认模型</option>
                  {replacementProfiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.name}（{profile.model_name}）
                    </option>
                  ))}
                </select>
              </section>
            )}

            <section className="rounded-2xl border border-stone-200/80 bg-white/70 px-4 py-4 text-sm leading-6 text-stone-600">
              <p>API Key、服务地址和模型级提示词会被清除。</p>
              <p className="mt-2">
                发信模板不会删除。历史邮件、任务和分析记录会保留。
              </p>
              <p className="mt-2">
                暂停或失败的任务不会自动继续。再次运行时，请选择可用模型。
              </p>
            </section>
          </div>

          <div className="relative mt-6 flex flex-wrap justify-end gap-3">
            <button
              type="button"
              className="inline-flex items-center justify-center rounded-2xl border border-stone-200 bg-white px-4 py-2.5 text-sm font-medium text-stone-700 transition hover:border-stone-300 hover:bg-stone-50 hover:text-stone-900 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={busy}
              onClick={dismiss}
            >
              {impact.can_delete ? "取消" : "知道了"}
            </button>
            {impact.can_delete && (
              <button
                type="button"
                className="inline-flex items-center justify-center gap-2 rounded-2xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm shadow-red-200/90 transition hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={busy}
                onClick={onConfirm}
              >
                {busy ? (
                  <Loader2
                    aria-hidden="true"
                    className="h-4 w-4 animate-spin"
                  />
                ) : (
                  <Trash2 aria-hidden="true" className="h-4 w-4" />
                )}
                {busy ? "正在删除" : "确认删除"}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
