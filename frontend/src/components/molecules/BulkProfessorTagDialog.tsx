import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { Check, Loader2, Plus, Tags } from "lucide-react";
import type {
  ProfessorBulkTagModeDTO,
  ProfessorTagDTO,
  ProfessorTagPayloadDTO,
} from "@/types";

type BulkProfessorTagDialogProps = {
  open: boolean;
  selectedCount: number;
  tags: ProfessorTagDTO[];
  saving?: boolean;
  creating?: boolean;
  onSave: (payload: { mode: ProfessorBulkTagModeDTO; tagIds: number[] }) => void;
  onCreateTag: (payload: ProfessorTagPayloadDTO) => Promise<ProfessorTagDTO | null>;
  onClose: () => void;
};

const DEFAULT_TEXT_COLOR = "#166534";
const DEFAULT_BACKGROUND_COLOR = "#dcfce7";

const modeLabels: Record<ProfessorBulkTagModeDTO, string> = {
  add: "追加标签",
  remove: "移除标签",
  replace: "覆盖标签",
};

export const BulkProfessorTagDialog = ({
  open,
  selectedCount,
  tags,
  saving = false,
  creating = false,
  onSave,
  onCreateTag,
  onClose,
}: BulkProfessorTagDialogProps) => {
  const [mode, setMode] = useState<ProfessorBulkTagModeDTO>("add");
  const [selectedTagIds, setSelectedTagIds] = useState<number[]>([]);
  const [creatingCustomTag, setCreatingCustomTag] = useState(false);
  const [name, setName] = useState("");
  const [textColor, setTextColor] = useState(DEFAULT_TEXT_COLOR);
  const [backgroundColor, setBackgroundColor] = useState(
    DEFAULT_BACKGROUND_COLOR,
  );
  const createInFlightRef = useRef(false);
  const busy = saving || creating;
  const saveDisabled = busy || (mode !== "replace" && selectedTagIds.length === 0);

  useEffect(() => {
    if (!open) {
      return;
    }
    setMode("add");
    setSelectedTagIds([]);
    setCreatingCustomTag(false);
    setName("");
    setTextColor(DEFAULT_TEXT_COLOR);
    setBackgroundColor(DEFAULT_BACKGROUND_COLOR);
  }, [open]);

  if (!open) {
    return null;
  }

  const toggleTag = (tagId: number) => {
    setSelectedTagIds((previous) =>
      previous.includes(tagId)
        ? previous.filter((item) => item !== tagId)
        : [...previous, tagId],
    );
  };

  const handleCreate = async () => {
    const trimmedName = name.trim();
    if (!trimmedName || busy || createInFlightRef.current) {
      return;
    }
    createInFlightRef.current = true;
    try {
      const createdTag = await onCreateTag({
        name: trimmedName,
        text_color: textColor,
        background_color: backgroundColor,
      });
      if (createdTag && !selectedTagIds.includes(createdTag.id)) {
        setSelectedTagIds((previous) => [...previous, createdTag.id]);
      }
      if (createdTag) {
        setName("");
        setTextColor(DEFAULT_TEXT_COLOR);
        setBackgroundColor(DEFAULT_BACKGROUND_COLOR);
        setCreatingCustomTag(false);
      }
    } finally {
      createInFlightRef.current = false;
    }
  };

  return (
    <div
      role="dialog"
      aria-label="批量修改导师标签"
      aria-modal="true"
      className="fixed inset-0 z-[80] flex items-center justify-center bg-stone-950/35 p-4 backdrop-blur-md"
      onClick={() => {
        if (!busy) {
          onClose();
        }
      }}
    >
      <div
        className="w-full max-w-lg rounded-[28px] border border-stone-200 bg-white p-5 shadow-[0_28px_72px_-32px_rgba(41,37,36,0.55)]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-stone-900">
              批量修改导师标签
            </h2>
            <p className="mt-1 text-sm text-stone-500">
              将应用到 {selectedCount} 位导师
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-stone-300 hover:text-stone-900 disabled:cursor-not-allowed disabled:opacity-60"
            aria-label="关闭批量标签修改"
          >
            ×
          </button>
        </div>

        <div className="mt-5 grid grid-cols-3 gap-2 rounded-2xl bg-stone-100 p-1">
          {(["add", "remove", "replace"] satisfies ProfessorBulkTagModeDTO[]).map(
            (item) => (
              <button
                key={item}
                type="button"
                aria-label={`切换为${modeLabels[item]}`}
                aria-pressed={mode === item}
                onClick={() => setMode(item)}
                disabled={busy}
                className={clsx(
                  "inline-flex min-h-9 items-center justify-center rounded-xl px-3 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-60",
                  mode === item
                    ? "bg-white text-stone-900 shadow-sm"
                    : "text-stone-600 hover:text-stone-900",
                )}
              >
                {modeLabels[item]}
              </button>
            ),
          )}
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          {tags.map((tag) => {
            const selected = selectedTagIds.includes(tag.id);
            return (
              <button
                key={tag.id}
                type="button"
                aria-label={`选择标签 ${tag.name}`}
                aria-pressed={selected}
                disabled={busy}
                onClick={() => toggleTag(tag.id)}
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
            );
          })}
        </div>

        {tags.length === 0 ? (
          <div className="mt-5 rounded-2xl border border-stone-200 bg-stone-50 px-4 py-3 text-sm text-stone-500">
            暂无可选标签，可先新增一个标签。
          </div>
        ) : null}

        <div className="mt-5">
          <button
            type="button"
            onClick={() => setCreatingCustomTag((previous) => !previous)}
            disabled={busy}
            className="ui-btn-secondary px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Plus className="h-4 w-4" />
            新增标签
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
            onClick={() => onSave({ mode, tagIds: selectedTagIds })}
            disabled={saveDisabled}
            className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
          >
            {saving ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Tags className="h-4 w-4" />
            )}
            {modeLabels[mode]}
          </button>
        </div>
      </div>
    </div>
  );
};
