import { ArrowLeft } from 'lucide-react';

interface TaskSubmitActionsProps {
  isSubmitting: boolean;
  onCancel: () => void;
  onSubmit: () => void;
}

export const TaskSubmitActions: React.FC<TaskSubmitActionsProps> = ({
  isSubmitting,
  onCancel,
  onSubmit,
}) => {
  return (
    <div className="flex items-center justify-between border-t border-stone-100 pt-6">
      <button
        type="button"
        onClick={onCancel}
        disabled={isSubmitting}
        className="inline-flex items-center gap-2 rounded-xl border border-stone-200 bg-white px-6 py-2.5 text-sm font-semibold text-stone-600 transition-all hover:border-stone-300 hover:text-stone-800 disabled:opacity-50"
      >
        <ArrowLeft className="h-4 w-4" />
        返回
      </button>

      <button
        type="button"
        onClick={onSubmit}
        disabled={isSubmitting}
        className="inline-flex items-center gap-2 rounded-xl bg-primary px-8 py-2.5 text-sm font-semibold text-white shadow-md transition-all hover:bg-primary-dark hover:shadow-lg disabled:opacity-60"
      >
        {isSubmitting ? '创建中…' : '创建任务'}
      </button>
    </div>
  );
};
