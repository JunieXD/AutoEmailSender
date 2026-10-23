import type { ReactNode } from "react";

/** Fills an empty or failed area with what happened and what to do next. */
export function Notice({ title, detail, children }: { title: string; detail: string; children?: ReactNode }) {
  return (
    <div className="grid h-full place-items-center p-6 text-center">
      <div>
        <p className="text-lg font-medium">{title}</p>
        <p className="mt-1 text-sm text-muted-foreground">{detail}</p>
        {children}
      </div>
    </div>
  );
}
