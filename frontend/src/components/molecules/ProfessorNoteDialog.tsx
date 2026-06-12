import { useEffect, useState } from "react";
import { Loader2, X } from "lucide-react";
import { useDismissableLayerClick } from "@/lib/useDismissableLayerClick";

type ProfessorNoteDialogProfessor = {
  id: number;
  name: string;
  university?: string | null;
  school?: string | null;
};

type ProfessorNoteDialogProps = {
  open: boolean;
  professor: ProfessorNoteDialogProfessor | null;
  initialNote: string | null;
  saving: boolean;
  onSave: (note: string) => void;
  onClose: () => void;
};

const getProfessorAffiliation = (professor: ProfessorNoteDialogProfessor) =>
  [professor.university, professor.school].filter(Boolean).join(" / ") ||
  "未填写学校 / 学院";

export const ProfessorNoteDialog = ({
  open,
  professor,
  initialNote,
  saving,
  onSave,
  onClose,
}: ProfessorNoteDialogProps) => {
  const [note, setNote] = useState(initialNote ?? "");
  const {
    onBackdropClick,
    onBackdropMouseDown,
    onContentClick,
    onContentMouseDown,
  } = useDismissableLayerClick(onClose);

  useEffect(() => {
    if (open) {
      setNote(initialNote ?? "");
    }
  }, [initialNote, open, professor?.id]);

  useEffect(() => {
    if (!open) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving) {
        onClose();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open, saving]);

  if (!open || !professor) {
    return null;
  }

  return (
    <div
      role="dialog"
      aria-label="编辑个人备注"
      aria-modal="true"
      className="fixed inset-0 z-[80] flex items-center justify-center bg-stone-950/35 p-4 backdrop-blur-md"
      onClick={(event) => {
        if (!saving) {
          onBackdropClick(event);
        }
      }}
      onMouseDown={onBackdropMouseDown}
    >
      <div
        className="w-full max-w-lg rounded-[28px] border border-stone-200 bg-white p-5 shadow-[0_28px_72px_-32px_rgba(41,37,36,0.55)]"
        onClick={onContentClick}
        onMouseDown={onContentMouseDown}
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-stone-900">
              编辑个人备注
            </h2>
            <div className="mt-2">
              <div className="truncate text-sm font-medium text-stone-900">
                {professor.name}
              </div>
              <div className="mt-1 text-sm text-stone-500">
                {getProfessorAffiliation(professor)}
              </div>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-stone-300 hover:text-stone-900 disabled:cursor-not-allowed disabled:opacity-60"
            aria-label="关闭个人备注编辑"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <label className="mt-5 block">
          <span className="mb-2 block text-sm font-medium text-stone-700">
            个人备注
          </span>
          <textarea
            aria-label="个人备注"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            disabled={saving}
            className="min-h-40 w-full resize-y rounded-2xl border border-stone-200 bg-stone-50/60 px-4 py-3 text-sm leading-6 text-stone-800 outline-none transition placeholder:text-stone-400 focus:border-primary focus:bg-white focus:ring-2 focus:ring-primary/15 disabled:cursor-not-allowed disabled:opacity-70"
            placeholder="记录只对自己有用的判断、沟通偏好或跟进线索。"
          />
        </label>

        <div className="mt-5 flex flex-wrap justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => onSave(note)}
            disabled={saving}
            className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            保存备注
          </button>
        </div>
      </div>
    </div>
  );
};
