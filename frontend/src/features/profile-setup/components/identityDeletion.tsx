import {
  MODAL_BACKDROP_CLASS_NAME,
  MODAL_SURFACE_CLASS_NAME,
} from "@/components/atoms/modalStyles";
import { useDismissableLayerClick } from "@/lib/useDismissableLayerClick";
import { useDocumentScrollLock } from "@/lib/useDocumentScrollLock";
import {
  type IdentityDeletionImpactDTO,
  type IdentityReferenceCountsDTO,
} from "@/types";
import { AlertTriangle, Loader2, Trash2, X } from "lucide-react";
import { useCallback, useEffect } from "react";

const IDENTITY_REFERENCE_LABELS: Array<
  [keyof IdentityReferenceCountsDTO, string]
> = [
  ["email_tasks", "邮件任务"],
  ["email_logs", "邮件与通信记录"],
  ["batch_tasks", "批量任务"],
  ["test_compose_sessions", "测试写信会话"],
  ["test_compose_messages", "测试邮件记录"],
  ["match_analysis_jobs", "匹配分析任务"],
  ["match_analysis_runs", "匹配运行记录"],
  ["match_results", "导师匹配结果"],
  ["delivery_attempts", "邮件投递尝试"],
  ["email_observations", "邮件投递观测记录"],
  ["agent_change_plans", "Agent 操作计划"],
];

export const IdentityDeletionDialog = ({
  impact,
  busy,
  onClose,
  onConfirm,
}: {
  impact: IdentityDeletionImpactDTO;
  busy: boolean;
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

  const references = IDENTITY_REFERENCE_LABELS.filter(
    ([key]) => impact.references[key] > 0,
  );

  return (
    <div
      className={`${MODAL_BACKDROP_CLASS_NAME} z-[90]`}
      onClick={onBackdropClick}
      onMouseDown={onBackdropMouseDown}
    >
      <div
        aria-describedby="identity-deletion-description"
        aria-labelledby="identity-deletion-title"
        aria-modal="true"
        className={`${MODAL_SURFACE_CLASS_NAME} flex max-h-[min(88vh,46rem)] w-full max-w-2xl flex-col`}
        onClick={onContentClick}
        onMouseDown={onContentMouseDown}
        role="dialog"
      >
        <div className="absolute inset-x-0 top-0 h-24 bg-[radial-gradient(circle_at_top,rgba(251,191,36,0.18),transparent_68%)]" />
        <div className="relative flex items-start justify-between gap-4 border-b border-stone-200/80 px-6 py-6">
          <div className="flex min-w-0 items-start gap-3">
            <div className="mt-0.5 flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-red-100 text-red-600 shadow-sm shadow-red-100/80">
              <AlertTriangle aria-hidden="true" className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <h2
                id="identity-deletion-title"
                className="text-lg font-semibold tracking-[0.01em] text-stone-900"
              >
                {impact.can_delete ? "删除身份配置" : "暂时无法删除身份配置"}
              </h2>
              <p
                id="identity-deletion-description"
                className="mt-2 text-sm leading-6 text-stone-600"
              >
                “{impact.identity_name}” · {impact.email_address}
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

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-5">
          {impact.blockers.length > 0 && (
            <section className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-rose-900">
                <AlertTriangle aria-hidden="true" className="h-4 w-4" />
                以下操作结束前无法删除
              </div>
              <p className="mt-2 text-sm leading-6 text-rose-800">
                这些操作正在执行，直接删除可能导致邮件发送或任务结果异常。请按提示找到并处理对应任务。
              </p>
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

          <section>
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
                没有关联的业务历史。
              </p>
            )}
          </section>

          {(impact.automatic_actions.cancel_email_task_ids.length > 0 ||
            impact.automatic_actions.stop_batch_task_ids.length > 0 ||
            impact.automatic_actions.cancel_match_analysis_job_ids.length > 0 ||
            impact.automatic_actions.invalidate_agent_change_plan_ids.length >
              0) && (
            <section>
              <h3 className="text-sm font-semibold text-stone-900">
                确认后自动处理
              </h3>
              <ul className="mt-2 space-y-2 text-sm leading-6 text-stone-600">
                {impact.automatic_actions.cancel_email_task_ids.length > 0 && (
                  <li>
                    取消未开始发送的邮件任务：ID{" "}
                    {impact.automatic_actions.cancel_email_task_ids.join("、")}
                  </li>
                )}
                {impact.automatic_actions.stop_batch_task_ids.length > 0 && (
                  <li>
                    停止仍可继续的批量任务：ID{" "}
                    {impact.automatic_actions.stop_batch_task_ids.join("、")}
                  </li>
                )}
                {impact.automatic_actions.cancel_match_analysis_job_ids.length >
                  0 && (
                  <li>
                    取消匹配分析任务：ID{" "}
                    {impact.automatic_actions.cancel_match_analysis_job_ids.join(
                      "、",
                    )}
                  </li>
                )}
                {impact.automatic_actions.invalidate_agent_change_plan_ids
                  .length > 0 && (
                  <li>
                    作废关联的待确认 Agent 计划：ID{" "}
                    {impact.automatic_actions.invalidate_agent_change_plan_ids.join(
                      "、",
                    )}
                  </li>
                )}
              </ul>
            </section>
          )}

          <section>
            <h3 className="text-sm font-semibold text-stone-900">处理方式</h3>
            <ul className="mt-2 space-y-2 text-sm leading-6 text-stone-600">
              {impact.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </section>
        </div>

        <div className="relative flex flex-wrap justify-end gap-3 border-t border-stone-200/80 px-6 py-5">
          <button
            type="button"
            className="ui-btn-secondary rounded-2xl"
            disabled={busy}
            onClick={dismiss}
          >
            {impact.can_delete ? "取消" : "知道了"}
          </button>
          {impact.can_delete && (
            <button
              type="button"
              className="ui-btn-danger rounded-2xl"
              disabled={busy}
              onClick={onConfirm}
            >
              {busy ? (
                <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
              ) : (
                <Trash2 aria-hidden="true" className="h-4 w-4" />
              )}
              {busy ? "正在删除" : "确认删除"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
