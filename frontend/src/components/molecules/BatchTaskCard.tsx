import { Clock, Target, Play, Pause, Square, FileText } from 'lucide-react';
import { TaskStatusBadge, type TaskStatus } from '../atoms/TaskStatusBadge';
import { ProgressBar } from '../atoms/ProgressBar';
import clsx from 'clsx';

export interface BatchTask {
  id: string;
  name: string;
  schedule: string;
  targetDesc: string;
  currentCount: number;
  totalCount: number;
  status: TaskStatus;
}

interface BatchTaskCardProps {
  task: BatchTask;
  onPause: (id: string) => void;
  onResume: (id: string) => void;
  onStop: (id: string) => void;
  onView: (id: string) => void;
}

export const BatchTaskCard: React.FC<BatchTaskCardProps> = ({ task, onPause, onResume, onStop, onView }) => {
  const btnBase = 'flex items-center gap-1.5 px-4 py-1.5 text-sm font-medium rounded-lg transition-all active:scale-95';
  const btnOutline = clsx(
    btnBase,
    'border border-stone-200 text-stone-600 hover:bg-stone-50 hover:text-primary hover:border-red-200',
  );
  const btnSolidPrimary = clsx(
    btnBase,
    'bg-primary text-white shadow-md transition-all duration-300 hover:bg-primary-dark hover:shadow-lg hover:shadow-primary/30 hover:-translate-y-0.5',
  );
  const btnDangerOutline = clsx(btnBase, 'border border-stone-200 text-red-500 hover:bg-red-50 hover:border-red-200');

  return (
    <div className="flex flex-col bg-white rounded-2xl border border-stone-200 shadow-sm hover:shadow-md transition-shadow p-6 relative overflow-hidden group">
      <div className="flex justify-between items-start gap-4 mb-4">
        <h3 className="text-lg font-bold text-stone-800 line-clamp-2 leading-snug" title={task.name}>
          {task.name}
        </h3>
        <TaskStatusBadge status={task.status} />
      </div>

      <div className="flex flex-col gap-2.5 mb-2">
        <div className="flex items-start gap-2 text-stone-500 text-sm">
          <Clock className="w-4 h-4 mt-0.5 shrink-0" />
          <span>{task.schedule}</span>
        </div>
        <div className="flex items-start gap-2 text-stone-500 text-sm">
          <Target className="w-4 h-4 mt-0.5 shrink-0" />
          <span>{task.targetDesc}</span>
        </div>
      </div>

      <ProgressBar current={task.currentCount} total={task.totalCount} status={task.status} />

      <div className="mt-6 pt-4 border-t border-stone-100 flex items-center justify-end gap-3">
        {task.status === '运行中' && (
          <>
            <button onClick={() => onPause(task.id)} className={btnOutline}>
              <Pause className="w-4 h-4" /> 暂停
            </button>
            <button onClick={() => onStop(task.id)} className={btnDangerOutline}>
              <Square className="w-4 h-4" /> 终止
            </button>
            <button onClick={() => onView(task.id)} className={btnOutline}>
              详情
            </button>
          </>
        )}

        {task.status === '已暂停' && (
          <>
            <button onClick={() => onStop(task.id)} className={btnDangerOutline}>
              <Square className="w-4 h-4" /> 终止
            </button>
            <button onClick={() => onView(task.id)} className={btnOutline}>
              详情
            </button>
            <button onClick={() => onResume(task.id)} className={btnSolidPrimary}>
              <Play className="w-4 h-4" fill="currentColor" /> 继续
            </button>
          </>
        )}

        {task.status === '已完成' && (
          <button onClick={() => onView(task.id)} className={btnOutline}>
            <FileText className="w-4 h-4" /> 查看报告
          </button>
        )}
      </div>
    </div>
  );
};
