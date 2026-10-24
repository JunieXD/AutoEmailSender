import { Check, LoaderCircle, X } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { errorMessage } from "@/api/call";
import { Dialog } from "@/components/ui/dialog";

// Pieces of the settings dialogs.

/** Closes a form's dialog, but not while it saves, nor on a stray click or Esc with input in it. */
export function formClose(
  dirty: boolean,
  pending: boolean,
  close: () => void,
): NonNullable<ComponentProps<typeof Dialog>["onOpenChange"]> {
  return (open, details) => {
    // docs/development.md#界面: what the user typed is not thrown away by a stray click or Esc,
    // nor while it is being saved.
    const stray = details.reason === "outside-press" || details.reason === "escape-key";
    if (!open && !pending && !(dirty && stray)) close();
  };
}

export function Field({ label, id, children }: { label: string; id: string; children: ReactNode }) {
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-sm text-muted-foreground">
        {label}
      </label>
      {children}
    </div>
  );
}

/** How a connection test went: under way, passed, or why it failed. */
export function TestResult({
  label,
  mutation,
}: {
  /** What was tested. */
  label: string;
  mutation: { status: "idle" | "pending" | "success" | "error"; error: Error | null };
}) {
  if (mutation.status === "idle") return null;
  const [icon, text] = {
    pending: [<LoaderCircle key="i" className="animate-spin text-muted-foreground" />, "正在测试…"],
    success: [<Check key="i" className="text-success" />, "正常"],
    error: [<X key="i" className="text-destructive" />, errorMessage(mutation.error)],
  }[mutation.status];
  return (
    <p className="flex gap-2 [&_svg]:mt-0.5 [&_svg]:size-4 [&_svg]:shrink-0">
      {icon}
      <span>
        {label}：{text}
      </span>
    </p>
  );
}
