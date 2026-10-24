import { Paperclip, X } from "lucide-react";
import { fileSize } from "@/format";

/** The files an email carries, with their total when there are several; removable while writing. */
export function Attachments<T extends { fileName: string; size: number }>({
  files,
  onRemove,
}: {
  files: T[];
  onRemove?: (file: T) => void;
}) {
  if (files.length === 0) return null;
  return (
    <ul className="flex flex-wrap items-center gap-2 text-sm">
      {files.map((f) => (
        <li
          key={f.fileName}
          className={`flex min-w-0 items-center gap-1.5 rounded-md border border-border py-1 pl-2 ${onRemove ? "pr-1" : "pr-2.5"}`}
        >
          <Paperclip className="size-3.5 shrink-0 text-muted-foreground" strokeWidth={1.8} />
          <span className="truncate">{f.fileName}</span>
          <span className="shrink-0 text-muted-foreground">{fileSize(f.size)}</span>
          {onRemove && (
            <button
              type="button"
              aria-label={`去掉附件“${f.fileName}”`}
              className="grid size-5 shrink-0 place-items-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
              onClick={() => onRemove(f)}
            >
              <X className="size-3.5" strokeWidth={1.8} />
            </button>
          )}
        </li>
      ))}
      {files.length > 1 && (
        <li className="text-muted-foreground">共 {fileSize(files.reduce((sum, f) => sum + f.size, 0))}</li>
      )}
    </ul>
  );
}
