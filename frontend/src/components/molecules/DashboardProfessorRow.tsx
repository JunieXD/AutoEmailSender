import clsx from "clsx";
import { Loader2, Sparkles } from "lucide-react";
import { ProfessorNoteButton } from "@/components/molecules/ProfessorNoteButton";
import { ProfessorTagChips } from "@/components/molecules/ProfessorTagChips";
import { SelectionToggleButton } from "@/components/molecules/SelectionToggleButton";
import {
  formatProfessorSearchField,
  isProfessorSearchFieldEmpty,
} from "@/lib/professorSearchField";
import type { ProfessorDashboardItemDTO } from "@/types";

export type DashboardProfessorRowTimeHighlight = "sent" | "replied" | null;

type DashboardProfessorRowProps = {
  professor: ProfessorDashboardItemDTO;
  selected: boolean;
  bulkDisabled: boolean;
  scoring: boolean;
  canCalculateMatch: boolean;
  statusLabel: string;
  timeHighlight: DashboardProfessorRowTimeHighlight;
  timeLabel: string | null;
  onToggleSelection: () => void;
  onCalculateMatch: () => void;
  onOpenWorkspace: () => void;
  onEditNote?: () => void;
  onAddTag?: () => void;
};

const formatMatchLabel = (score: number | null) =>
  score === null ? "匹配 未计算" : `匹配 ${score}%`;

const formatSentLabel = (sentCount: number) =>
  sentCount === 0 ? "未发送" : `已发送 ${sentCount} 次`;

const joinNonEmpty = (values: Array<string | null | undefined>) =>
  values
    .map((value) => value?.trim() ?? "")
    .filter(Boolean)
    .join(" / ");

const getRowBackgroundClass = (
  selected: boolean,
  timeHighlight: DashboardProfessorRowTimeHighlight,
) => {
  if (selected) {
    return "bg-primary/5";
  }
  if (timeHighlight === "sent") {
    return "bg-emerald-50 hover:bg-emerald-100/70";
  }
  if (timeHighlight === "replied") {
    return "bg-emerald-50 hover:bg-emerald-100/70";
  }
  return "bg-white hover:bg-[#fcfbf8]";
};

export const DashboardProfessorRow = ({
  professor,
  selected,
  bulkDisabled,
  scoring,
  canCalculateMatch,
  statusLabel,
  timeHighlight,
  timeLabel,
  onToggleSelection,
  onCalculateMatch,
  onOpenWorkspace,
  onEditNote,
  onAddTag,
}: DashboardProfessorRowProps) => (
  <article
    data-testid={`dashboard-professor-row-${professor.id}`}
    className={clsx(
      "grid gap-4 px-6 py-5 transition lg:grid-cols-[minmax(0,1.35fr)_minmax(0,0.95fr)_auto] lg:items-center",
      getRowBackgroundClass(selected, timeHighlight),
    )}
  >
    <div className="flex min-w-0 items-center gap-8">
      <SelectionToggleButton
        label={`选择 ${professor.name}`}
        selected={selected}
        onToggle={onToggleSelection}
      />
      <div className="min-w-0">
        <div
          data-testid="dashboard-professor-name-line"
          className="flex min-w-0 flex-wrap items-center gap-2"
        >
          <div className="min-w-0 truncate text-base font-semibold text-stone-900">
            {professor.name}
          </div>
          <ProfessorNoteButton
            professorName={professor.name}
            personalNote={professor.personal_note}
            onEdit={onEditNote ?? (() => undefined)}
          />
          <ProfessorTagChips
            tags={professor.tags}
            maxVisible={2}
            onAddTag={onAddTag}
          />
        </div>
        {isProfessorSearchFieldEmpty(
          joinNonEmpty([professor.title, professor.university, professor.school]),
        ) ? null : (
          <div className="mt-1 text-sm text-stone-500">
            {formatProfessorSearchField(
              joinNonEmpty([professor.title, professor.university, professor.school]),
            )}
          </div>
        )}
        {isProfessorSearchFieldEmpty(professor.research_direction) ? null : (
          <p className="mt-2 line-clamp-2 text-sm leading-6 text-stone-600">
            {formatProfessorSearchField(professor.research_direction)}
          </p>
        )}
      </div>
    </div>

    <div className="flex flex-wrap gap-2 lg:justify-start">
      <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1.5 text-sm font-medium text-stone-700">
        {formatMatchLabel(professor.match_score)}
      </span>
      <span className="rounded-full border border-stone-200 bg-white px-3 py-1.5 text-sm font-medium text-stone-600">
        {formatSentLabel(professor.sent_count)}
      </span>
      {timeLabel ? (
        <span className="rounded-full border border-stone-200 bg-white/80 px-3 py-1.5 text-sm font-medium text-stone-700">
          {timeLabel}
        </span>
      ) : null}
      {professor.has_active_schedule ? (
        <span className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1.5 text-sm font-medium text-amber-800">
          已排程
        </span>
      ) : null}
      <span className="rounded-full border border-primary/20 bg-primary/10 px-3 py-1.5 text-sm font-medium text-primary">
        {statusLabel}
      </span>
    </div>

    <div className="flex flex-wrap items-center gap-3 lg:justify-end">
      <button
        type="button"
        onClick={onCalculateMatch}
        disabled={bulkDisabled || scoring || !canCalculateMatch}
        className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
      >
        {scoring ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Sparkles className="h-4 w-4" />
        )}
        {canCalculateMatch ? "分析匹配度" : "缺少研究信息"}
      </button>
      <button
        type="button"
        onClick={onOpenWorkspace}
        disabled={bulkDisabled}
        className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
      >
        打开工作区
      </button>
    </div>
  </article>
);
