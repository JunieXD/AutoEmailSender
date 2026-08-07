import { Activity, CalendarClock } from 'lucide-react';

export type TaskCenterSection = 'delivery' | 'background';

export const TaskCenterSectionSwitch = ({
  activeSection,
  onChange,
}: {
  activeSection: TaskCenterSection;
  onChange: (section: TaskCenterSection) => void;
}) => (
  <div className="inline-flex gap-1 rounded-2xl border border-stone-200 bg-white p-1 shadow-sm">
    <button
      type="button"
      onClick={() => onChange('delivery')}
      className={
        activeSection === 'delivery'
          ? 'inline-flex min-h-9 items-center gap-2 rounded-xl bg-primary px-4 text-sm font-medium text-white shadow-sm shadow-primary/20'
          : 'inline-flex min-h-9 items-center gap-2 rounded-xl px-4 text-sm font-medium text-stone-600 hover:bg-stone-50'
      }
    >
      <CalendarClock className="h-4 w-4" />
      发送计划
    </button>
    <button
      type="button"
      onClick={() => onChange('background')}
      className={
        activeSection === 'background'
          ? 'inline-flex min-h-9 items-center gap-2 rounded-xl bg-primary px-4 text-sm font-medium text-white shadow-sm shadow-primary/20'
          : 'inline-flex min-h-9 items-center gap-2 rounded-xl px-4 text-sm font-medium text-stone-600 hover:bg-stone-50'
      }
    >
      <Activity className="h-4 w-4" />
      后台任务
    </button>
  </div>
);
