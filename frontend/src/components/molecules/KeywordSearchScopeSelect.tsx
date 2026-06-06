import { useEffect, useId, useRef, useState } from "react";
import clsx from "clsx";
import { Check, ChevronDown, Search } from "lucide-react";

export type KeywordSearchScopeOption<TValue extends string = string> = {
  value: TValue;
  label: string;
};

type KeywordSearchScopeSelectProps<TValue extends string = string> = {
  label: string;
  options: ReadonlyArray<KeywordSearchScopeOption<TValue>>;
  selectedValues: TValue[];
  disabled?: boolean;
  onChange: (nextValues: TValue[]) => void;
};

const getSummary = <TValue extends string>(
  options: ReadonlyArray<KeywordSearchScopeOption<TValue>>,
  selectedValues: TValue[],
) =>
  selectedValues.length === options.length
    ? "全部字段"
    : `已选 ${selectedValues.length} 项`;

export const KeywordSearchScopeSelect = <TValue extends string = string>({
  label,
  options,
  selectedValues,
  disabled = false,
  onChange,
}: KeywordSearchScopeSelectProps<TValue>) => {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const listboxId = useId();
  const selectedSet = new Set(selectedValues);
  const summary = getSummary(options, selectedValues);

  useEffect(() => {
    if (!open) {
      return;
    }

    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };

    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        aria-label={`${label}：${summary}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        onClick={() => setOpen((previous) => !previous)}
        className={clsx(
          "inline-flex h-8 items-center gap-2 rounded-xl border border-stone-200 bg-stone-50 px-3 text-xs font-medium text-stone-700 transition hover:border-primary/40 hover:text-primary",
          disabled && "cursor-not-allowed opacity-60",
          open &&
            "border-primary/45 bg-white text-primary ring-2 ring-primary/10",
        )}
      >
        <Search className="h-3.5 w-3.5" />
        <span>{summary}</span>
        <ChevronDown
          className={clsx(
            "h-3.5 w-3.5 transition",
            open && "rotate-180",
          )}
        />
      </button>

      {open ? (
        <div className="absolute right-0 top-[calc(100%+0.45rem)] z-40 w-48 overflow-hidden rounded-2xl border border-stone-200/90 bg-white p-1 shadow-[0_22px_40px_-26px_rgba(41,37,36,0.34)]">
          <div
            id={listboxId}
            role="listbox"
            aria-label={label}
            aria-multiselectable="true"
            className="flex max-h-64 flex-col gap-1 overflow-y-auto py-1"
          >
            {options.map((option) => {
              const selected = selectedSet.has(option.value);
              const locked = selected && selectedValues.length === 1;

              return (
                <button
                  key={option.value}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() => {
                    if (locked) {
                      return;
                    }

                    if (selected) {
                      onChange(
                        selectedValues.filter((value) => value !== option.value),
                      );
                    } else {
                      onChange([...selectedValues, option.value]);
                    }
                  }}
                  className={clsx(
                    "flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2 text-left text-[13px] leading-5 transition",
                    selected
                      ? "bg-primary text-white shadow-sm shadow-primary/25"
                      : "text-stone-700 hover:bg-stone-100/90 hover:text-stone-900",
                    locked && "cursor-default opacity-80",
                  )}
                >
                  <span className="truncate">{option.label}</span>
                  {selected ? <Check className="h-4 w-4 shrink-0" /> : null}
                </button>
              );
            })}
          </div>
          <div className="border-t border-stone-100 px-3 py-2 text-xs text-stone-500">
            至少保留最后一项
          </div>
        </div>
      ) : null}
    </div>
  );
};
