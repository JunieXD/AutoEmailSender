import clsx from "clsx";
import type { ReactNode } from "react";
import { Archive, PencilLine, RotateCcw } from "lucide-react";
import { ProfessorNoteButton } from "@/components/molecules/ProfessorNoteButton";
import { ProfessorTagChips } from "@/components/molecules/ProfessorTagChips";
import { SelectionToggleButton } from "@/components/molecules/SelectionToggleButton";
import { formatApiDateTime } from "@/lib/dateTime";
import { normalizeProfessorTitleDisplay } from "@/lib/professorTitle";
import type { ProfessorManagementItemDTO } from "@/types";

type ManagementProfessorRowProps = {
  professor: ProfessorManagementItemDTO;
  checked: boolean;
  selectable: boolean;
  tableColumns: string;
  onToggleSelection: () => void;
  onEdit: () => void;
  onArchive: () => void;
  onRestore: () => void;
  onEditNote?: () => void;
  onPrimaryTagSelect?: (tagId: number) => void;
  onAddTag?: () => void;
};

type FieldCellProps = {
  label: string;
  valueClassName?: string;
  children: ReactNode;
};

const FieldCell = ({ label, valueClassName, children }: FieldCellProps) => (
  <div className="min-w-0 rounded-2xl bg-stone-50/70 px-3 py-2 lg:bg-transparent lg:px-0 lg:py-0">
    <div className="mb-1 text-[11px] font-medium text-stone-400 lg:hidden">
      {label}
    </div>
    <div className={valueClassName}>{children}</div>
  </div>
);

export const ManagementProfessorRow = ({
  professor,
  checked,
  selectable,
  tableColumns,
  onToggleSelection,
  onEdit,
  onArchive,
  onRestore,
  onEditNote,
  onPrimaryTagSelect,
  onAddTag,
}: ManagementProfessorRowProps) => {
  const schoolAndCollege = [professor.university, professor.school]
    .filter(Boolean)
    .join(" / ");

  return (
    <article
      className={clsx(
        "px-6 py-5 transition",
        professor.archived_at ? "bg-stone-50/65" : "bg-white",
      )}
    >
      <div className={clsx("grid gap-4 lg:items-center", tableColumns)}>
        <div className="flex items-center justify-center">
          <SelectionToggleButton
            label={`选择 ${professor.name}`}
            selected={checked}
            disabled={!selectable}
            onToggle={onToggleSelection}
          />
        </div>

        <div className="min-w-0 lg:text-center">
          <div
            data-testid="management-professor-name-line"
            className="flex min-w-0 flex-wrap items-center justify-start gap-1.5 lg:justify-center"
          >
            <div className="min-w-0 truncate text-base font-semibold text-stone-900 lg:text-center">
              {professor.name}
            </div>
            <ProfessorNoteButton
              professorName={professor.name}
              personalNote={professor.personal_note}
              onEdit={onEditNote ?? (() => undefined)}
            />
            <ProfessorTagChips
              tags={professor.tags}
              maxVisible={1}
              className="flex-nowrap justify-start lg:justify-center"
              onTagClick={onPrimaryTagSelect}
              onAddTag={onAddTag}
            />
          </div>
          {professor.archived_at ? (
            <span className="mt-2 inline-flex rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-[11px] font-medium text-amber-700">
              已删除
            </span>
          ) : null}
        </div>

        <FieldCell
          label="职称"
          valueClassName="text-sm text-stone-600 lg:text-center"
        >
          {normalizeProfessorTitleDisplay(professor.title) || "未填写职称"}
        </FieldCell>

        <FieldCell
          label="邮箱"
          valueClassName="break-all text-sm text-stone-700 lg:text-center"
        >
          {professor.email || "未填写邮箱"}
        </FieldCell>

        <FieldCell
          label="学校 / 学院"
          valueClassName="break-words text-sm text-stone-600 lg:text-center"
        >
          {schoolAndCollege || "未填写学校 / 学院"}
        </FieldCell>

        <FieldCell
          label="研究方向"
          valueClassName="line-clamp-3 text-sm leading-6 text-stone-600 lg:text-center"
        >
          {professor.research_direction || "未填写研究方向"}
        </FieldCell>

        <FieldCell
          label="更新时间"
          valueClassName="text-sm text-stone-500 lg:text-center"
        >
          <div className="lg:text-center">
            {formatApiDateTime(professor.updated_at)}
          </div>
          {professor.archived_at ? (
            <div className="mt-2 text-xs text-amber-700">
              删除于 {formatApiDateTime(professor.archived_at)}
            </div>
          ) : null}
        </FieldCell>

        <div className="grid w-full max-w-[12rem] grid-cols-2 gap-2 lg:mx-auto lg:justify-center">
          <button
            type="button"
            onClick={onEdit}
            className="ui-btn-secondary justify-center whitespace-nowrap px-3 py-2"
          >
            <PencilLine className="h-4 w-4" />
            编辑
          </button>
          {professor.archived_at ? (
            <button
              type="button"
              onClick={onRestore}
              className="ui-btn-secondary justify-center whitespace-nowrap px-3 py-2"
            >
              <RotateCcw className="h-4 w-4" />
              恢复
            </button>
          ) : (
            <button
              type="button"
              onClick={onArchive}
              className="ui-btn-danger justify-center whitespace-nowrap px-3 py-2"
            >
              <Archive className="h-4 w-4" />
              删除
            </button>
          )}
        </div>
      </div>
    </article>
  );
};
