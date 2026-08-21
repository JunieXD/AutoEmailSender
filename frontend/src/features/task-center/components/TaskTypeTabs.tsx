import { Bot, FileSearch, Mail, Sparkles } from "lucide-react";
import type { TasksTab } from "../model/taskCenterFilters";

type TaskTypeTabsProps = {
  activeTab: TasksTab;
  hasTaskSelection: boolean;
  counts: Record<TasksTab, number>;
  onChange: (tab: TasksTab) => void;
};

const tabs = [
  { id: "batch", label: "批量邮件", icon: Mail, requiresSelection: true },
  { id: "crawl", label: "智能抓取", icon: FileSearch, requiresSelection: false },
  { id: "match", label: "匹配分析", icon: Sparkles, requiresSelection: true },
  { id: "enrichment", label: "信息补全", icon: Bot, requiresSelection: false },
] as const;

export const TaskTypeTabs = ({
  activeTab,
  hasTaskSelection,
  counts,
  onChange,
}: TaskTypeTabsProps) => (
  <div className="inline-flex max-w-full gap-2 overflow-x-auto rounded-2xl border border-stone-200 bg-white p-1 shadow-sm">
    {tabs.map(({ id, label, icon: Icon, requiresSelection }) => {
      const active = activeTab === id;
      const disabled = requiresSelection && !hasTaskSelection;
      return (
        <button
          key={id}
          type="button"
          aria-label={label}
          disabled={disabled}
          onClick={() => onChange(id)}
          className={
            active
              ? "inline-flex min-h-9 shrink-0 items-center gap-2 whitespace-nowrap rounded-xl bg-primary px-5 text-sm font-medium text-white"
              : "inline-flex min-h-9 shrink-0 items-center gap-2 whitespace-nowrap rounded-xl px-5 text-sm font-medium text-stone-600 hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent"
          }
        >
          <Icon className="h-4 w-4" />
          {label}
          <span className={active ? "text-white/80" : "text-stone-400"}>
            {counts[id]}
          </span>
        </button>
      );
    })}
  </div>
);
