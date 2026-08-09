import { Loader2, RotateCcw, X } from 'lucide-react';
import { formatApiDateTime } from '@/lib/dateTime';
import {
  getOutreachGenerationModeLabel,
  getOutreachTemplateSourceLabel,
} from '@/features/batch-tasks/client/batchTaskDisplay';
import { PROFESSOR_STATUS_LABELS, type BatchTaskResendContextDTO } from '@/types';
import { SelectionToggleButton } from '@/components/molecules/SelectionToggleButton';

type BatchTaskResendDialogProps = {
  context: BatchTaskResendContextDTO | null;
  loading: boolean;
  selectedProfessorIds: number[];
  onSelectAll: () => void;
  onClear: () => void;
  onToggleProfessor: (professorId: number) => void;
  onClose: () => void;
  onSubmit: () => void;
};

export const BatchTaskResendDialog = ({
  context,
  loading,
  selectedProfessorIds,
  onSelectAll,
  onClear,
  onToggleProfessor,
  onClose,
  onSubmit,
}: BatchTaskResendDialogProps) => {
  const selectableItems = context?.items.filter((item) => item.selectable && item.professor_id !== null) ?? [];
  const selectedCount = selectedProfessorIds.length;
  const templateLabel = context
    ? getOutreachTemplateSourceLabel(context.defaults)
    : '正在加载';
  const generationModeLabel = getOutreachGenerationModeLabel(
    context?.defaults.outreach_generation_mode,
  );
  const selectedItems = selectableItems.filter(
    (item) => item.professor_id !== null && selectedProfessorIds.includes(item.professor_id),
  );
  const reusableCount = selectedItems.filter(
    (item) => item.content_reuse_kind !== 'regenerate',
  ).length;
  const regenerateCount = selectedItems.length - reusableCount;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-stone-950/35 p-4">
      <section
        role="dialog"
        aria-label="重新发起未成功项"
        className="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-3xl bg-white shadow-2xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-stone-200 bg-[#fcfbf8] px-6 py-5">
          <div>
            <div className="flex items-center gap-2 text-xs font-medium text-stone-500">
              <RotateCcw className="h-4 w-4 text-primary" />
              重新发起未成功项
            </div>
            <h3 className="mt-2 text-lg font-semibold text-stone-900">
              {context?.task.name ?? '批量任务'}
            </h3>
            <p className="mt-2 text-sm text-stone-500">
              可重新发起 {selectableItems.length} 位，已选 {selectedCount} 位
            </p>
          </div>
          <button type="button" onClick={onClose} className="ui-btn-secondary" aria-label="关闭重新发起面板">
            <X className="h-4 w-4" />
            关闭
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          {context ? (
            <section className="mb-4 rounded-2xl border border-primary/15 bg-primary/5 px-4 py-4">
              <div className="text-sm font-semibold text-stone-900">原任务发信设置</div>
              <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
                <div className="rounded-xl border border-white/80 bg-white/80 px-3 py-2">
                  <dt className="text-xs text-stone-500">发信模板</dt>
                  <dd className="mt-1 font-medium text-stone-900">{templateLabel}</dd>
                </div>
                <div className="rounded-xl border border-white/80 bg-white/80 px-3 py-2">
                  <dt className="text-xs text-stone-500">写信方式</dt>
                  <dd className="mt-1 font-medium text-stone-900">{generationModeLabel}</dd>
                </div>
              </dl>
              <p className="mt-3 text-xs leading-5 text-stone-600">
                选择导师，下一步再选内容策略。
              </p>
              <p className="mt-2 text-xs font-medium text-stone-700">
                有可沿用内容 {reusableCount} 封 · 没有可沿用内容 {regenerateCount} 封
              </p>
            </section>
          ) : null}
          {loading ? (
            <div className="flex items-center justify-center gap-2 rounded-2xl border border-stone-200 px-6 py-12 text-sm text-stone-500">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在加载可重新发起项…
            </div>
          ) : selectableItems.length === 0 ? (
            <p className="rounded-2xl border border-dashed border-stone-200 px-4 py-6 text-center text-sm text-stone-500">
              当前任务没有可重新发起的导师。
            </p>
          ) : (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3">
                <p className="text-sm text-amber-900">
                  可重新发起 {selectableItems.length} 位，已选 {selectedCount} 位
                </p>
                <div className="flex flex-wrap gap-2">
                  <button type="button" onClick={onSelectAll} className="ui-btn-secondary px-3 py-2 text-sm">
                    全选可发起
                  </button>
                  <button type="button" onClick={onClear} className="ui-btn-secondary px-3 py-2 text-sm">
                    清空选择
                  </button>
                </div>
              </div>

              {selectableItems.map((item) => {
                const professorId = item.professor_id as number;
                const checked = selectedProfessorIds.includes(professorId);
                return (
                  <label
                    key={item.email_task_id}
                    className="flex cursor-pointer items-start gap-3 rounded-2xl border border-stone-100 px-4 py-3 transition hover:border-primary/25 hover:bg-primary/5"
                  >
                    <SelectionToggleButton
                      label={`选择导师 ${item.professor_name}`}
                      selected={checked}
                      semantics="checkbox"
                      size="sm"
                      className="mt-1"
                      onToggle={() => onToggleProfessor(professorId)}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium text-stone-900">{item.professor_name}</span>
                        <span className="rounded-full bg-stone-100 px-2.5 py-1 text-xs text-stone-600">
                          {PROFESSOR_STATUS_LABELS[item.status]}
                        </span>
                        <span className="rounded-full bg-red-50 px-2.5 py-1 text-xs text-red-700">
                          {item.reason_label}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-stone-500">
                        {item.professor_email ?? '暂无邮箱'} · 更新 {formatApiDateTime(item.updated_at)}
                      </p>
                      <p className="mt-1 text-xs font-medium text-primary">
                        {item.content_reuse_kind === 'approved'
                          ? item.content_requires_review
                            ? '沿用上次保存内容，仍需审核'
                            : '沿用上次已批准内容'
                          : item.content_reuse_kind === 'generated'
                            ? '沿用上次 AI 草稿，仍需审核'
                            : item.content_reuse_kind === 'rewrite_source'
                              ? '沿用改写前草稿，仍需审核'
                              : '没有可用草稿，将重新生成'}
                      </p>
                    </div>
                  </label>
                );
              })}
            </div>
          )}
        </div>

        <div className="flex flex-wrap justify-end gap-3 border-t border-stone-200 px-6 py-4">
          <button type="button" onClick={onClose} className="ui-btn-secondary">
            继续选择
          </button>
          <button
            type="button"
            onClick={onSubmit}
            disabled={loading || selectedCount === 0}
            className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
          >
            去创建新任务
          </button>
        </div>
      </section>
    </div>
  );
};
