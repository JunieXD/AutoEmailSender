import { useState } from "react";
import { StickyNote } from "lucide-react";

type ProfessorNoteButtonProps = {
  professorName: string;
  personalNote?: string | null;
  onEdit: () => void;
};

export const ProfessorNoteButton = ({
  professorName,
  personalNote,
  onEdit,
}: ProfessorNoteButtonProps) => {
  const [previewOpen, setPreviewOpen] = useState(false);
  const trimmedNote = personalNote?.trim();

  if (!trimmedNote) {
    return null;
  }

  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setPreviewOpen(true)}
      onMouseLeave={() => setPreviewOpen(false)}
    >
      <button
        type="button"
        aria-label={`编辑${professorName}的个人备注`}
        onClick={(event) => {
          event.stopPropagation();
          onEdit();
        }}
        onFocus={() => setPreviewOpen(true)}
        onBlur={() => setPreviewOpen(false)}
        className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-amber-200 bg-amber-50 text-amber-700 transition hover:border-amber-300 hover:bg-amber-100 focus:outline-none focus:ring-2 focus:ring-amber-300/60"
      >
        <StickyNote className="h-3.5 w-3.5" />
      </button>
      {previewOpen ? (
        <span
          role="dialog"
          aria-label={`${professorName}的个人备注`}
          className="absolute left-0 top-[calc(100%+0.35rem)] z-50 max-h-60 w-[min(22.5rem,80vw)] overflow-y-auto whitespace-pre-wrap rounded-2xl border border-amber-100 bg-white p-3 text-left text-sm leading-6 text-stone-700 shadow-[0_18px_42px_-24px_rgba(41,37,36,0.45)]"
        >
          {trimmedNote}
        </span>
      ) : null}
    </span>
  );
};
