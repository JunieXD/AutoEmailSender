import { useEffect, useId, useRef, useState } from "react";
import clsx from "clsx";
import { Check, ChevronDown, X } from "lucide-react";

type MultiSelectFilterProps = {
  label: string;
  allLabel: string;
  selectedValues: string[];
  options: string[];
  disabled?: boolean;
  onToggle: (value: string) => void;
  onClear: () => void;
};

const getSummary = (selectedValues: string[], allLabel: string): string => {
  if (selectedValues.length === 0) {
    return allLabel;
  }
  if (selectedValues.length === 1) {
    return selectedValues[0];
  }
  return `${selectedValues[0]} 等 ${selectedValues.length} 项`;
};

export const MultiSelectFilter = ({
  label,
  allLabel,
  selectedValues,
  options,
  disabled = false,
  onToggle,
  onClear,
}: MultiSelectFilterProps) => {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const listboxId = useId();
  const selectedSet = new Set(selectedValues);
  const summary = getSummary(selectedValues, allLabel);

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
    <div ref={rootRef} className="block">
      <div className="mb-2 text-sm font-medium text-stone-800">{label}</div>
      <div className="relative">
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
            "ui-select-shell w-full",
            disabled && "cursor-not-allowed opacity-60",
            open && "border-primary/45 bg-white shadow-lg shadow-stone-300/25 ring-2 ring-primary/10",
          )}
        >
          <span className="flex-1 truncate text-left text-sm text-stone-700">
            {summary}
          </span>
          <ChevronDown
            className={clsx(
              "ui-select-chevron",
              open && "rotate-180 text-primary",
            )}
          />
        </button>

        {open ? (
          <div className="absolute left-0 top-[calc(100%+0.45rem)] z-40 w-full overflow-hidden rounded-2xl border border-stone-200/90 bg-white p-1 shadow-[0_22px_40px_-26px_rgba(41,37,36,0.34)]">
            <div className="flex items-center justify-between border-b border-stone-100 px-2 py-1.5">
              <span className="text-xs font-medium text-stone-500">
                已选 {selectedValues.length} 项
              </span>
              <button
                type="button"
                aria-label={`清空${label}筛选`}
                onClick={onClear}
                disabled={selectedValues.length === 0}
                className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-stone-500 hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <X className="h-3.5 w-3.5" />
                清空
              </button>
            </div>
            <div
              id={listboxId}
              role="listbox"
              aria-label={label}
              aria-multiselectable="true"
              className="flex max-h-60 flex-col gap-1 overflow-y-auto py-1"
            >
              {options.length === 0 ? (
                <div className="px-3 py-2 text-sm text-stone-400">暂无选项</div>
              ) : (
                options.map((option) => {
                  const selected = selectedSet.has(option);
                  return (
                    <button
                      key={option}
                      type="button"
                      role="option"
                      aria-selected={selected}
                      onClick={() => onToggle(option)}
                      className={clsx(
                        "flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2 text-left text-[13px] leading-5 transition",
                        selected
                          ? "bg-primary text-white shadow-sm shadow-primary/25"
                          : "text-stone-700 hover:bg-stone-100/90 hover:text-stone-900",
                      )}
                    >
                      <span className="truncate">{option}</span>
                      {selected ? <Check className="h-4 w-4 shrink-0" /> : null}
                    </button>
                  );
                })
              )}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
};
