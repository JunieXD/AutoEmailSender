import { Loader2, RotateCcw, X } from 'lucide-react';
import { formatApiDateTime } from '@/lib/dateTime';
import { PROFESSOR_STATUS_LABELS, type BatchTaskResendContextDTO } from '@/types';

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
          {loading ? (
            <div className="flex items-center justify-center gap-2 rounded-2xl border border-stone-200 px-6 py-12 text-sm text-stone-500">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在加载可重新发起项...
            </div>
          ) : selectableItems.length === 0 ? (
            <p className="rounded-2xl border border-dashed border-stone-200 px-4 py-6 text-center text-sm text-stone-500">
              当前任务没有可重新发起的老师。
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
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => onToggleProfessor(professorId)}
                      aria-label={`选择老师 ${item.professor_name}`}
                      className="mt-1 h-4 w-4 rounded border-stone-300 text-primary focus:ring-primary/30"
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