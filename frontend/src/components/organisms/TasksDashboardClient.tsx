import { useState } from 'react';
import { Plus } from 'lucide-react';
import { BatchTaskCard, type BatchTask } from '../molecules/BatchTaskCard';
import { PrimaryFillButton } from '../atoms/PrimaryFillButton';
import { useNotification } from '@/context/NotificationContext';
import { useConfirmDialog } from '@/lib/useConfirmDialog';

interface TasksDashboardClientProps {
  initialTasks: BatchTask[];
}

export const TasksDashboardClient: React.FC<TasksDashboardClientProps> = ({ initialTasks }) => {
  const [tasks, setTasks] = useState<BatchTask[]>(initialTasks);
  const { notifyWarning } = useNotification();
  const { confirm, dialog: confirmDialog } = useConfirmDialog();

  const handlePause = (id: string) => setTasks(tasks.map((t) => (t.id === id ? { ...t, status: '已暂停' } : t)));
  const handleResume = (id: string) => setTasks(tasks.map((t) => (t.id === id ? { ...t, status: '运行中' } : t)));
  const handleStop = async (id: string) => {
    const shouldStop = await confirm({
      title: '确认终止这个任务？',
      description: '终止后当前批次不会继续推进生成、排程和发送。',
      confirmLabel: '确认终止',
      cancelLabel: '继续保留',
      tone: 'danger',
    });
    if (!shouldStop) {
      return;
    }
    setTasks(tasks.map((t) => (t.id === id ? { ...t, status: '已完成' } : t)));
  };

  const handleView = (id: string) => notifyWarning('功能暂未开放', `查看任务 ${id} 详情（待开发）`);
  const handleCreateTask = () => notifyWarning('功能暂未开放', '新建批量任务（待开发）');

  return (
    <div className="min-h-[calc(100vh-64px)] bg-background p-8">
      {confirmDialog}
      <div className="max-w-6xl mx-auto">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold text-stone-800 tracking-wide mb-1">发送任务大厅</h1>
            <p className="text-stone-500 font-medium">管理自动化套磁任务进度与调度策略</p>
          </div>

          <PrimaryFillButton
            label="新建批量任务"
            icon={<Plus className="w-5 h-5" />}
            onClick={handleCreateTask}
          />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {tasks.map((task) => (
            <BatchTaskCard
              key={task.id}
              task={task}
              onPause={handlePause}
              onResume={handleResume}
              onStop={handleStop}
              onView={handleView}
            />
          ))}
        </div>
      </div>
    </div>
  );
};
