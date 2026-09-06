import { labelClassName } from "@/features/profile-setup/model/formControls";
import { type ProfileSetupSectionId } from "@/features/profile-setup/model/profileForms";
import { openExternalHttpUrl } from "@/lib/externalUrls";
import clsx from "clsx";
import { ChevronDown, ExternalLink } from "lucide-react";
import { type ReactNode, type TransitionEvent } from "react";

export const FieldLabel = ({
  label,
  required = false,
}: {
  label: string;
  required?: boolean;
}) => (
  <span className={labelClassName}>
    {required && (
      <span aria-hidden="true" className="text-base leading-none text-red-500">
        *
      </span>
    )}
    <span>{label}</span>
  </span>
);

export const ContextualHelpLink = ({
  href,
  children,
  tone = "quiet",
  compact = false,
}: {
  href: string;
  children: ReactNode;
  tone?: "quiet" | "surface";
  compact?: boolean;
}) => (
  <a
    href={href}
    target="_blank"
    rel="noopener noreferrer"
    onClick={(event) => {
      event.preventDefault();
      openExternalHttpUrl(href);
    }}
    className={clsx(
      "inline-flex shrink-0 items-center gap-1.5 text-xs font-medium text-primary underline decoration-primary/30 underline-offset-4 transition hover:bg-primary/5 hover:decoration-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25",
      compact
        ? "min-h-0 rounded px-1 py-0 leading-5"
        : "min-h-9 rounded-xl px-2.5",
      tone === "surface" &&
        "border border-stone-200 bg-white/90 no-underline shadow-sm hover:border-primary/25 hover:bg-white",
    )}
  >
    <span>{children}</span>
    <ExternalLink aria-hidden="true" className="h-3.5 w-3.5" />
  </a>
);

export const FormFieldHeader = ({
  id,
  label,
  required = false,
  help,
}: {
  id: string;
  label: string;
  required?: boolean;
  help?: ReactNode;
}) => (
  <div className="mb-2 flex min-h-[22px] flex-wrap items-center justify-start gap-x-1 gap-y-1">
    <label
      htmlFor={id}
      className="inline-flex items-center gap-1 text-sm font-medium text-stone-800"
    >
      {required && (
        <span
          aria-hidden="true"
          className="text-base leading-none text-red-500"
        >
          *
        </span>
      )}
      <span>{label}</span>
    </label>
    {help}
  </div>
);

export function ProfileSetupSection({
  sectionId,
  title,
  description,
  badge,
  helpAction,
  open,
  renderContent,
  onToggle,
  onExitComplete,
  sectionRef,
  children,
}: {
  sectionId: ProfileSetupSectionId;
  title: string;
  description: string;
  badge: ReactNode;
  helpAction?: ReactNode;
  open: boolean;
  renderContent: boolean;
  onToggle: () => void;
  onExitComplete: () => void;
  sectionRef: (element: HTMLElement | null) => void;
  children: ReactNode;
}) {
  const handleContentTransitionEnd = (
    event: TransitionEvent<HTMLDivElement>,
  ) => {
    if (open || event.propertyName !== "grid-template-rows") {
      return;
    }
    onExitComplete();
  };

  return (
    <section
      ref={sectionRef}
      className="overflow-hidden rounded-2xl border border-stone-200 bg-white shadow-sm"
    >
      <div
        onClick={(event) => {
          if (
            event.target instanceof Element &&
            event.target.closest("a, button")
          ) {
            return;
          }
          onToggle();
        }}
        className="cursor-pointer px-6 py-5 transition hover:bg-stone-50 active:bg-stone-50"
      >
        <button
          type="button"
          aria-expanded={open}
          aria-controls={`${sectionId}-setup-content`}
          onClick={onToggle}
          className="collapsible-card-toggle flex w-full min-w-0 items-center justify-between gap-4 text-left active:bg-stone-50"
        >
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold text-stone-900">{title}</h2>
            {badge}
          </div>
          <ChevronDown
            className={clsx(
              "h-5 w-5 shrink-0 text-stone-500 transition-transform",
              open ? "rotate-180" : "rotate-0",
            )}
          />
        </button>
        {description || helpAction ? (
          <div className="mt-2 flex flex-wrap items-center gap-x-1 gap-y-1">
            {description ? (
              <p className="text-xs leading-5 text-stone-600 sm:text-sm sm:leading-6">
                {description}
              </p>
            ) : null}
            {helpAction}
          </div>
        ) : null}
      </div>

      {renderContent ? (
        <div
          id={`${sectionId}-setup-content`}
          data-state={open ? "open" : "closed"}
          onTransitionEnd={handleContentTransitionEnd}
          className="collapsible-card-content"
        >
          <div className="collapsible-card-body min-h-0 px-6">{children}</div>
        </div>
      ) : null}
    </section>
  );
}
