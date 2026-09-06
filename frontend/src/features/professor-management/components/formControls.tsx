import {
  MODAL_BACKDROP_CLASS_NAME,
  MODAL_SURFACE_CLASS_NAME,
} from "@/components/atoms/modalStyles";
import {
  normalizeExternalHttpUrl,
  openExternalHttpUrl,
} from "@/lib/externalUrls";
import { useDismissableLayerClick } from "@/lib/useDismissableLayerClick";
import { useDocumentScrollLock } from "@/lib/useDocumentScrollLock";
import clsx from "clsx";
import { ExternalLink } from "lucide-react";
import { useEffect, type ReactNode } from "react";

const fieldLabelClassName =
  "mb-2 inline-flex items-center gap-1 text-sm font-medium text-stone-800";

export const inputClassName =
  "w-full rounded-2xl border border-stone-200 bg-white px-3 py-2.5 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15";

const urlInputWithActionClassName =
  "w-full rounded-2xl border border-stone-200 bg-white py-2.5 pl-3 pr-11 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15";

export const FieldLabel = ({
  label,
  required = false,
}: {
  label: string;
  required?: boolean;
}) => (
  <span className={fieldLabelClassName}>
    {required ? (
      <span className="text-base leading-none text-red-500">*</span>
    ) : null}
    <span>{label}</span>
  </span>
);

export const UrlInputField = ({
  id,
  label,
  value,
  placeholder,
  openLabel,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  placeholder: string;
  openLabel: string;
  onChange: (value: string) => void;
}) => {
  const openableUrl = normalizeExternalHttpUrl(value);

  return (
    <div className="block">
      <label htmlFor={id}>{<FieldLabel label={label} />}</label>
      <div className="relative">
        <input
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className={urlInputWithActionClassName}
          placeholder={placeholder}
        />
        <button
          type="button"
          aria-label={openLabel}
          title={openLabel}
          disabled={!openableUrl}
          onClick={() => {
            if (!openableUrl) {
              return;
            }
            openExternalHttpUrl(openableUrl);
          }}
          className="absolute right-1.5 top-1/2 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-xl border border-stone-200 bg-stone-50 text-stone-500 transition hover:border-primary/40 hover:bg-white hover:text-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-45"
        >
          <ExternalLink className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
};

export const ModalShell = ({
  open,
  title,
  description,
  onClose,
  children,
  headerAction,
  maxWidthClassName = "max-w-3xl",
}: {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  headerAction?: ReactNode;
  maxWidthClassName?: string;
}) => {
  const {
    onBackdropClick,
    onBackdropMouseDown,
    onContentClick,
    onContentMouseDown,
  } = useDismissableLayerClick(onClose);
  useDocumentScrollLock(open);

  useEffect(() => {
    if (!open) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);

  if (!open) {
    return null;
  }

  return (
    <div
      role="dialog"
      aria-label={title}
      aria-modal="true"
      className={`${MODAL_BACKDROP_CLASS_NAME} z-[80]`}
      onClick={onBackdropClick}
      onMouseDown={onBackdropMouseDown}
    >
      <div
        className={clsx(
          `${MODAL_SURFACE_CLASS_NAME} w-full`,
          maxWidthClassName,
        )}
        onClick={onContentClick}
        onMouseDown={onContentMouseDown}
      >
        <div
          data-testid="professor-modal-scroll"
          className="relative max-h-[85vh] overflow-y-auto overscroll-contain px-6 py-6"
        >
          <div className="min-w-0">
            <div className="flex min-w-0 items-start justify-between gap-4">
              <h2 className="min-w-0 break-words text-2xl font-semibold tracking-[0.01em] text-stone-900">
                {title}
              </h2>
              {headerAction ? (
                <div className="shrink-0">{headerAction}</div>
              ) : null}
            </div>
            {description ? (
              <p className="mt-2 max-w-2xl text-sm leading-6 text-stone-600">
                {description}
              </p>
            ) : null}
          </div>
          {children}
        </div>
      </div>
    </div>
  );
};
