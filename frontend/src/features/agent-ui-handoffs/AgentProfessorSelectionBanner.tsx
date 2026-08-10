import { Bot, EyeOff, RotateCcw, X } from 'lucide-react';
import type { AgentProfessorSelectionMode } from './types';

export const AgentProfessorSelectionBanner = ({
  selectionCount,
  totalSelectedCount,
  selectionMode,
  selectedOnly,
  onExitSelectedOnly,
  onUndo,
  onClear,
}: {
  selectionCount: number;
  totalSelectedCount: number;
  selectionMode: AgentProfessorSelectionMode;
  selectedOnly: boolean;
  onExitSelectedOnly: () => void;
  onUndo: () => void;
  onClear: () => void;
}) => (
  <div
    data-testid="agent-professor-selection"
    role="status"
    className="flex flex-col gap-3 rounded-2xl border border-primary/20 bg-primary/5 px-4 py-3 text-sm text-stone-700 sm:flex-row sm:items-center sm:justify-between"
  >
    <div className="flex min-w-0 items-start gap-3">
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-primary text-white shadow-sm shadow-primary/20">
        <Bot className="h-4 w-4" />
      </span>
      <div className="min-w-0">
        <div className="font-semibold text-stone-900">
          Agent {selectionMode === 'add' ? '新增选择' : '已选择'} {selectionCount} 位导师
        </div>
        <div className="mt-0.5 text-xs leading-5 text-stone-500">
          当前共勾选 {totalSelectedCount} 位
          {selectedOnly ? ' · 仅显示 Agent 本次选择' : ' · 保留当前列表视图'}
        </div>
      </div>
    </div>
    <div className="flex shrink-0 flex-wrap gap-2">
      {selectedOnly ? (
        <button
          type="button"
          onClick={onExitSelectedOnly}
          className="ui-btn-secondary min-h-9 px-3 py-1.5 text-xs"
        >
          <EyeOff className="h-3.5 w-3.5" />
          退出仅看已选
        </button>
      ) : null}
      <button
        type="button"
        onClick={onUndo}
        className="ui-btn-secondary min-h-9 px-3 py-1.5 text-xs"
      >
        <RotateCcw className="h-3.5 w-3.5" />
        撤销 Agent 选择
      </button>
      <button
        type="button"
        onClick={onClear}
        className="ui-btn-secondary min-h-9 px-3 py-1.5 text-xs"
      >
        <X className="h-3.5 w-3.5" />
        清除全部选择
      </button>
    </div>
  </div>
);
