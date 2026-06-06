import { useState } from "react";
import { Check, Trash2 } from "lucide-react";
import clsx from "clsx";
import type { ProfessorTagDTO, ProfessorTagPayloadDTO } from "@/types";

type ProfessorTagSelectorProps = {
  tags: ProfessorTagDTO[];
  selectedTagIds: number[];
  disabled?: boolean;
  onChange: (tagIds: number[]) => void;
  onCreateTag: (payload: ProfessorTagPayloadDTO) => void;
  onDeleteTag: (tag: ProfessorTagDTO) => void;
};

const DEFAULT_TEXT_COLOR = "#166534";
const DEFAULT_BACKGROUND_COLOR = "#dcfce7";

export const ProfessorTagSelector = ({
  tags,
  selectedTagIds,
  disabled = false,
  onChange,
  onCreateTag,
  onDeleteTag,
}: ProfessorTagSelectorProps) => {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [textColor, setTextColor] = useState(DEFAULT_TEXT_COLOR);
  const [backgroundColor, setBackgroundColor] = useState(
    DEFAULT_BACKGROUND_COLOR,
  );
  const [deleteMode, setDeleteMode] = useState(false);
  const [draggingTagId, setDraggingTagId] = useState<number | null>(null);
  const selectedSet = new Set(selectedTagIds);
  const tagById = new Map(tags.map((tag) => [tag.id, tag]));
  const orderedTags = [
    ...selectedTagIds
      .map((tagId) => tagById.get(tagId))
      .filter((tag): tag is ProfessorTagDTO => Boolean(tag)),
    ...tags.filter((tag) => !selectedSet.has(tag.id)),
  ];

  const toggleTag = (tagId: number) => {
    if (disabled) {
      return;
    }
    if (selectedSet.has(tagId)) {
      onChange(selectedTagIds.filter((id) => id !== tagId));
      return;
    }
    onChange([...selectedTagIds, tagId]);
  };

  const handleCreate = () => {
    const trimmedName = name.trim();
    if (!trimmedName || disabled) {
      return;
    }
    onCreateTag({
      name: trimmedName,
      text_color: textColor,
      background_color: backgroundColor,
    });
    setName("");
    setTextColor(DEFAULT_TEXT_COLOR);
    setBackgroundColor(DEFAULT_BACKGROUND_COLOR);
    setCreating(false);
  };

  const moveDraggedTagBefore = (targetTagId: number) => {
    if (disabled || draggingTagId === null || draggingTagId === targetTagId) {
      return;
    }
    const nextTagIds = selectedTagIds.filter((tagId) => tagId !== draggingTagId);
    const targetIndex = nextTagIds.indexOf(targetTagId);
    if (targetIndex < 0) {
      return;
    }
    nextTagIds.splice(targetIndex, 0, draggingTagId);
    onChange(nextTagIds);
  };

  return (
    <div className="rounded-[28px] border border-stone-200 bg-stone-50/60 p-5">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="text-base font-semibold text-stone-900">导师标签</div>
          <div className="mt-1 text-xs text-stone-500">
            {selectedTagIds.length === 0
              ? "暂无标签"
              : `已选择 ${selectedTagIds.length} 个标签`}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={disabled}
            onClick={() => setCreating((previous) => !previous)}
            className="ui-btn-secondary px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-60"
          >
            + 自定义标签
          </button>
          <button
            type="button"
            aria-pressed={deleteMode}
            disabled={disabled}
            onClick={() => setDeleteMode((previous) => !previous)}
            className={clsx(
              "inline-flex items-center gap-2 rounded-2xl border px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-60",
              deleteMode
                ? "border-red-200 bg-red-50 text-red-700"
                : "border-stone-200 bg-white text-stone-600 hover:border-red-200 hover:text-red-600",
            )}
          >
            <Trash2 className="h-4 w-4" />
            删除标签
          </button>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        {orderedTags.map((tag) => {
          const selected = selectedSet.has(tag.id);
          return (
            <span key={tag.id} className="inline-flex items-center overflow-hidden rounded-full">
              <button
                type="button"
                aria-label={`选择标签 ${tag.name}`}
                aria-pressed={selected}
                disabled={disabled}
                draggable={selected && !disabled}
                onDragStart={() => {
                  if (selected) {
                    setDraggingTagId(tag.id);
                  }
                }}
                onDragOver={(event) => {
                  if (selected) {
                    event.preventDefault();
                  }
                }}
                onDrop={() => {
                  if (selected) {
                    moveDraggedTagBefore(tag.id);
                    setDraggingTagId(null);
                  }
                }}
                onDragEnd={() => setDraggingTagId(null)}
                onClick={() => toggleTag(tag.id)}
                className={clsx(
                  "inline-flex min-h-8 items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-60",
                  selected
                    ? "border-primary/40 shadow-sm shadow-primary/10"
                    : "border-stone-200 hover:border-stone-300",
                )}
                style={{
                  backgroundColor: tag.background_color,
                  color: tag.text_color,
                }}
              >
                {selected ? <Check className="h-3.5 w-3.5" /> : null}
                {tag.name}
              </button>
              {deleteMode ? (
                <button
                  type="button"
                  aria-label={`删除标签 ${tag.name}`}
                  disabled={disabled}
                  onClick={() => onDeleteTag(tag)}
                  className="inline-flex h-8 w-8 -translate-x-1 animate-[tag-trash-in_160ms_ease-out] items-center justify-center rounded-full border border-red-100 bg-white text-red-500 opacity-100 transition duration-200 ease-out hover:border-red-200 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              ) : null}
            </span>
          );
        })}
      </div>

      {creating ? (
        <div className="mt-4 grid gap-3 rounded-2xl border border-stone-200 bg-white p-3 md:grid-cols-[minmax(0,1fr)_8rem_8rem_auto] md:items-end">
          <label className="block">
            <div className="mb-1 text-xs font-medium text-stone-600">标签名</div>
            <input
              aria-label="标签名"
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="w-full rounded-xl border border-stone-200 px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
              placeholder="例如：已联系"
            />
          </label>
          <label className="block">
            <div className="mb-1 text-xs font-medium text-stone-600">字体颜色</div>
            <input
              aria-label="字体颜色"
              type="color"
              value={textColor}
              onChange={(event) => setTextColor(event.target.value)}
              className="h-10 w-full rounded-xl border border-stone-200 bg-white px-2"
            />
          </label>
          <label className="block">
            <div className="mb-1 text-xs font-medium text-stone-600">背景颜色</div>
            <input
              aria-label="背景颜色"
              type="color"
              value={backgroundColor}
              onChange={(event) => setBackgroundColor(event.target.value)}
              className="h-10 w-full rounded-xl border border-stone-200 bg-white px-2"
            />
          </label>
          <button
            type="button"
            onClick={handleCreate}
            disabled={disabled || !name.trim()}
            className="ui-btn-primary justify-center disabled:cursor-not-allowed disabled:opacity-60"
          >
            创建标签
          </button>
        </div>
      ) : null}
    </div>
  );
};
