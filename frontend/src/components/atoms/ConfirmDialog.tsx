import { useEffect } from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";
import { AlertTriangle, X } from "lucide-react";

export type ConfirmDialogTone = "danger" | "neutral";

type ConfirmDialogProps = {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel?: string;
  secondaryLabel?: string;
  cancelLabel?: string | null;
  tone?: ConfirmDialogTone;
  onCancel: () => void;
  onConfirm: () => void;
  onSecondary?: () => void;
};

export const ConfirmDialog = ({
  open,
  title,
  description,
  confirmLabel = "确认",
  secondaryLabel,
  cancelLabel,
  tone = "neutral",
  onCancel,
  onConfirm,
  onSecondary,
}: ConfirmDialogProps) => {
  const resolvedCancelLabel = cancelLabel ?? "取消";
  const showCancelButton = cancelLabel !== null;

  useEffect(() => {
    if (!open) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onCancel();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onCancel, open]);

  if (!open) {
    return null;
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center bg-stone-950/35 p-4 backdrop-blur-md"
      onClick={onCancel}
    >
      <div
        className="relative w-full max-w-md overflow-hidden rounded-[30px] border border-stone-200/80 bg-[linear-gradient(180deg,rgba(255,252,246,0.98),rgba(255,245,233,0.95))] shadow-[0_34px_90px_-32px_rgba(41,37,36,0.5)]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="absolute inset-x-0 top-0 h-24 bg-[radial-gradient(circle_at_top,rgba(251,191,36,0.18),transparent_68%)]" />
        <div className="relative px-6 py-6">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <div
                className={clsx(
                  "mt-0.5 flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl shadow-sm",
                  tone === "danger"
                    ? "bg-red-100 text-red-600 shadow-red-100/80"
                    : "bg-amber-100 text-amber-700 shadow-amber-100/80",
                )}
              >
                <AlertTriangle className="h-5 w-5" />
              </div>
              <div>
                <h3 className="text-lg font-semibold tracking-[0.01em] text-stone-900">
                  {title}
                </h3>
                {description ? (
                  <p className="mt-2 text-sm leading-6 text-stone-600">
                    {description}
                  </p>
                ) : null}
              </div>
            </div>
            <button
              type="button"
              onClick={onCancel}
              className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-stone-200 bg-white/80 text-stone-500 transition hover:border-stone-300 hover:bg-white hover:text-stone-900"
              aria-label="关闭确认弹层"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="mt-6 flex flex-wrap justify-end gap-3">
            {showCancelButton ? (
              <button
                type="button"
                onClick={onCancel}
                className="inline-flex items-center justify-center rounded-2xl border border-stone-200 bg-white px-4 py-2.5 text-sm font-medium text-stone-700 transition hover:border-stone-300 hover:bg-stone-50 hover:text-stone-900"
              >
                {resolvedCancelLabel}
              </button>
            ) : null}
            {secondaryLabel ? (
              <button
                type="button"
                onClick={onSecondary}
                className="inline-flex items-center justify-center rounded-2xl border border-stone-200 bg-white px-4 py-2.5 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-stone-50 hover:text-stone-900"
              >
                {secondaryLabel}
              </button>
            ) : null}
            <button
              type="button"
              onClick={onConfirm}
              className={clsx(
                "inline-flex items-center justify-center rounded-2xl px-4 py-2.5 text-sm font-semibold text-white transition shadow-sm",
                tone === "danger"
                  ? "bg-red-600 shadow-red-200/90 hover:bg-red-700"
                  : "bg-primary shadow-primary/20 hover:bg-primary-dark",
              )}
            >
              {confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
};
