import clsx from "clsx";

export type ActionResultState = "idle" | "success" | "error";

export type IdentityConnectionTestSummary = {
  kind: "smtp" | "imap";
  status: "success" | "error";
  message: string;
  possibleCause?: string | null;
};

export type TestComposeSetupStatus =
  | "unchecked"
  | "loading"
  | "completed"
  | "pending";

export const inputClassName =
  "w-full rounded-xl border border-stone-200 px-3 py-2 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20";

export const labelClassName =
  "mb-2 inline-flex items-center gap-1 text-sm font-medium text-stone-800";

export const getActionButtonClassName = (
  state: ActionResultState,
  loading: boolean,
) =>
  clsx(
    "inline-flex items-center gap-2 rounded-xl border px-4 py-2 text-sm font-medium transition",
    state === "success" &&
      "border-emerald-200 bg-emerald-50 text-emerald-700 hover:border-emerald-300 hover:bg-emerald-100/80",
    state === "error" &&
      "border-red-200 bg-red-50 text-red-700 hover:border-red-300 hover:bg-red-100/80",
    state === "idle" &&
      "border-stone-200 bg-white text-stone-700 hover:border-stone-300 hover:bg-stone-50 hover:text-stone-900",
    loading && "cursor-not-allowed opacity-70",
  );
