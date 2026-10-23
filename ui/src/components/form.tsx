import { Check, LoaderCircle, X } from "lucide-react";
import type { ReactNode } from "react";
import { errorMessage } from "@/api/call";

// Pieces of the settings dialogs.

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
