import type { Change } from "@/diff";

export function Changes({ parts, className }: { parts: Change[]; className?: string }) {
  return (
    <p className={`leading-relaxed break-words whitespace-pre-wrap ${className ?? ""}`}>
      {parts.map((c, i) =>
        c.kind === "kept" ? (
          <span key={i}>{c.text}</span>
        ) : c.kind === "removed" ? (
          <del key={i} className={`rounded-sm text-muted-foreground ${c.fact ? "bg-highlight" : "bg-diff-delete"}`}>
            {c.text}
          </del>
        ) : (
          <ins key={i} className={`rounded-sm no-underline ${c.fact ? "bg-highlight" : "bg-diff-insert"}`}>
            {c.text}
          </ins>
        ),
      )}
    </p>
  );
}
