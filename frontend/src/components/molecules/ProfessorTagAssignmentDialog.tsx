import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { Check, Loader2, Plus, Trash2 } from "lucide-react";
import {
  MODAL_BACKDROP_CLASS_NAME,
  MODAL_SURFACE_CLASS_NAME,
} from "@/components/atoms/modalStyles";
import type { ProfessorTagDTO, ProfessorTagPayloadDTO } from "@/types";

type ProfessorTagAssignmentDialogProps = {
  open: boolean;
  scopeKey: string | number | null;
  professorName: string;
  tags: ProfessorTagDTO[];
  selectedTagIds: number[];
  saving?: boolean;
  creating?: boolean;
  onChange: (tagIds: number[]) => void;
  onCreateTag: (payload: ProfessorTagPayloadDTO) => Promise<ProfessorTagDTO | null>;
  onDeleteTag: (tag: ProfessorTagDTO) => void;
  onSave: () => void;
  onClose: () => void;
};

const DEFAULT_TEXT_COLOR = "#166534";
const DEFAULT_BACKGROUND_COLOR = "#dcfce7";

export const ProfessorTagAssignmentDialog = ({
  open,
  scopeKey,
  professorName,
  tags,
  selectedTagIds,
  saving = false,
  creating = false,
  onChange,
  onCreateTag,
  onDeleteTag,
  onSave,
  onClose,
}: ProfessorTagAssignmentDialogProps) => {
  const [creatingCustomTag, setCreatingCustomTag] = useState(false);
  const [deleteMode, setDeleteMode] = useState(false);
  const [name, setName] = useState("");
  const [textColor, setTextColor] = useState(DEFAULT_TEXT_COLOR);
  const [backgroundColor, setBackgroundColor] = useState(
    DEFAULT_BACKGROUND_COLOR,
  );
  const activeScopeRef = useRef<string | number | null>(scopeKey);
  const createInFlightRef = useRef(false);
  activeScopeRef.current = open ? scopeKey : null;
  const busy = saving || creating;

  const resetCreateForm = () => {
    setName("");
    setTextColor(DEFAULT_TEXT_COLOR);
    setBackgroundColor(DEFAULT_BACKGROUND_COLOR);
    setCreatingCustomTag(false);
    setDeleteMode(false);
  };

  useEffect(() => {
    resetCreateForm();
  }, [open, scopeKey]);

  if (!open) {
    return null;
  }

  const handleCreate = async () => {
    const trimmedName = name.trim();
    if (!trimmedName || busy || createInFlightRef.current) {
      return;
    }
    createInFlightRef.current = true;
    const requestScopeKey = scopeKey;
    try {
      const createdTag = await onCreateTag({
        name: trimmedName,
        text_color: textColor,
        background_color: backgroundColor,
      });
      if (!createdTag) {
        return;
      }
      if (activeScopeRef.current !== requestScopeKey) {
        return;
      }
      if (!selectedTagIds.includes(createdTag.id)) {
        onChange([...selectedTagIds, createdTag.id]);
      }
      resetCreateForm();
    } finally {
      createInFlightRef.current = false;
    }
  };

  return (
    <div
      role="dialog"
      aria-label="添加导师标签"
      aria-modal="true"
      className={`${MODAL_BACKDROP_CLASS_NAME} z-[80]`}
      onClick={() => {
        if (!busy) {
          onClose();
        }
      }}
    >
      <div
        className={`${MODAL_SURFACE_CLASS_NAME} w-full max-w-lg p-6`}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-stone-900">
              添加导师标签
            </h2>
            <p className="mt-1 text-sm text-stone-500">{professorName}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-stone-300 hover:text-stone-900 disabled:cursor-not-allowed disabled:opacity-60"
            aria-label="关闭标签选择"
          >
            ×
          </button>
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          {tags.map((tag) => {
            const selected = selectedTagIds.includes(tag.id);
            return (
              <span
                key={tag.id}
                className="inline-flex items-center overflow-hidden rounded-full"
              >
                <button
                  type="button"
                  aria-label={`选择标签 ${tag.name}`}
                  aria-pressed={selected}
                  disabled={busy}
                  onClick={() =>
                    onChange(
                      selected
                        ? selectedTagIds.filter((tagId) => tagId !== tag.id)
                        : [...selectedTagIds, tag.id],
                    )
                  }
                  className={clsx(
                    "inline-flex min-h-9 items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-60",
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
                    disabled={busy}
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

        {tags.length === 0 ? (
          <div className="mt-5 rounded-2xl border border-stone-200 bg-stone-50 px-4 py-3 text-sm text-stone-500">
            暂无可选标签，可先新增一个标签。
          </div>
        ) : null}

        <div className="mt-5 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={() => {
              setCreatingCustomTag((previous) => !previous);
              setDeleteMode(false);
            }}
            disabled={busy}
            className="ui-btn-secondary px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Plus className="h-4 w-4" />
            新增标签
          </button>
          <button
            type="button"
            onClick={() => {
              setDeleteMode((previous) => !previous);
              setCreatingCustomTag(false);
            }}
            disabled={busy || tags.length === 0}
            aria-pressed={deleteMode}
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

        {creatingCustomTag ? (
          <div className="mt-4 grid gap-3 rounded-2xl border border-stone-200 bg-stone-50 p-3 md:grid-cols-[minmax(0,1fr)_7rem_7rem]">
            <label className="block md:col-span-3">
              <div className="mb-1 text-xs font-medium text-stone-600">
                标签名
              </div>
              <input
                aria-label="新增标签名"
                value={name}
                onChange={(event) => setName(event.target.value)}
                className="w-full rounded-xl border border-stone-200 px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
                placeholder="例如：已联系"
              />
            </label>
            <label className="block">
              <div className="mb-1 text-xs font-medium text-stone-600">
                字体颜色
              </div>
              <input
                aria-label="新增标签字体颜色"
                type="color"
                value={textColor}
                onChange={(event) => setTextColor(event.target.value)}
                className="h-10 w-full rounded-xl border border-stone-200 bg-white px-2"
              />
            </label>
            <label className="block">
              <div className="mb-1 text-xs font-medium text-stone-600">
                背景颜色
              </div>
              <input
                aria-label="新增标签背景颜色"
                type="color"
                value={backgroundColor}
                onChange={(event) => setBackgroundColor(event.target.value)}
                className="h-10 w-full rounded-xl border border-stone-200 bg-white px-2"
              />
            </label>
            <button
              type="button"
              onClick={() => void handleCreate()}
              disabled={busy || !name.trim()}
              className="ui-btn-primary justify-center self-end disabled:cursor-not-allowed disabled:opacity-60"
            >
              {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              创建标签
            </button>
          </div>
        ) : null}

        <div className="mt-6 flex flex-wrap justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
          >
            取消
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={busy}
            className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
          >
            {saving ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
            保存标签
          </button>
        </div>
      </div>
    </div>
  );
};
