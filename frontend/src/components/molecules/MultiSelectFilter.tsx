import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import clsx from "clsx";
import {
  Check,
  ChevronDown,
  Search,
  Square,
  SquareCheck,
  X,
} from "lucide-react";

type MultiSelectFilterProps = {
  label: string;
  allLabel: string;
  selectedValues: string[];
  options: string[];
  optionLabels?: Record<string, string>;
  disabled?: boolean;
  onChange: (nextValues: string[]) => void;
};

const getSummary = (
  selectedValues: string[],
  allLabel: string,
  optionLabels: Record<string, string>,
): string => {
  if (selectedValues.length === 0) {
    return allLabel;
  }
  if (selectedValues.length === 1) {
    return optionLabels[selectedValues[0]] ?? selectedValues[0];
  }
  return `${optionLabels[selectedValues[0]] ?? selectedValues[0]} 等 ${selectedValues.length} 项`;
};

const areSameValues = (left: string[], right: string[]): boolean =>
  left.length === right.length &&
  left.every((value, index) => value === right[index]);

const expandSelectedValues = (options: string[], selectedValues: string[]) => {
  if (selectedValues.length === 0) {
    return [...options];
  }

  const selectedSet = new Set(selectedValues);
  return options.filter((option) => selectedSet.has(option));
};

const POPOVER_GAP_PX = 8;
const PREFERRED_POPOVER_HEIGHT_PX = 440;

export const MultiSelectFilter = ({
  label,
  allLabel,
  selectedValues,
  options,
  optionLabels = {},
  disabled = false,
  onChange,
}: MultiSelectFilterProps) => {
  const [open, setOpen] = useState(false);
  const [draftValues, setDraftValues] = useState<string[] | null>(null);
  const [draftSelectionChanged, setDraftSelectionChanged] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [openUpward, setOpenUpward] = useState(false);
  const [popoverMaxHeight, setPopoverMaxHeight] = useState(
    PREFERRED_POPOVER_HEIGHT_PX,
  );
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const previousOptionsRef = useRef(options);
  const previousSelectedRef = useRef(selectedValues);
  const listboxId = useId();
  const summary = getSummary(selectedValues, allLabel, optionLabels);
  const activeDraftValues = draftValues ?? [];
  const draftSet = new Set(activeDraftValues);
  const visibleOptions = useMemo(
    () => {
      const normalizedSearchTokens = searchQuery
        .trim()
        .toLocaleLowerCase()
        .split(/\s+/)
        .filter(Boolean);

      return options.filter((option) => {
        const searchableText = `${optionLabels[option] ?? option} ${option}`.toLocaleLowerCase();
        return normalizedSearchTokens.every((token) => searchableText.includes(token));
      });
    },
    [optionLabels, options, searchQuery],
  );
  const allOptionsSelected =
    options.length > 0 && options.every((option) => draftSet.has(option));
  const allVisibleOptionsSelected =
    visibleOptions.length > 0 &&
    visibleOptions.every((option) => draftSet.has(option));
  const searchScopesUnrestrictedSelection =
    selectedValues.length === 0 &&
    !draftSelectionChanged &&
    searchQuery.trim().length > 0;
  const valuesToApply = searchScopesUnrestrictedSelection
    ? visibleOptions.filter((option) => draftSet.has(option))
    : activeDraftValues;

  const updatePopoverLayout = useCallback(() => {
    const triggerRect = triggerRef.current?.getBoundingClientRect();
    if (!triggerRect) {
      return;
    }

    const appHeaderBottom = document
      .querySelector<HTMLElement>('[data-app-header="true"]')
      ?.getBoundingClientRect().bottom;
    const usableViewportTop = Math.min(
      window.innerHeight,
      Math.max(0, appHeaderBottom ?? 0),
    );
    const availableAbove = Math.max(
      0,
      triggerRect.top - usableViewportTop - POPOVER_GAP_PX,
    );
    const availableBelow = Math.max(
      0,
      window.innerHeight - triggerRect.bottom - POPOVER_GAP_PX,
    );
    const shouldOpenUpward =
      availableBelow < PREFERRED_POPOVER_HEIGHT_PX &&
      availableAbove > availableBelow;
    const availableHeight = shouldOpenUpward
      ? availableAbove
      : availableBelow;

    setOpenUpward(shouldOpenUpward);
    setPopoverMaxHeight(
      Math.min(PREFERRED_POPOVER_HEIGHT_PX, Math.floor(availableHeight)),
    );
  }, []);

  const closeMenu = useCallback((restoreFocus = false) => {
    setOpen(false);
    setDraftValues(null);
    setDraftSelectionChanged(false);
    setSearchQuery("");
    if (restoreFocus) {
      triggerRef.current?.focus();
    }
  }, []);

  useEffect(() => {
    if (!open) {
      return;
    }

    searchInputRef.current?.focus();
    updatePopoverLayout();

    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        closeMenu();
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeMenu(true);
      }
    };

    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("resize", updatePopoverLayout);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("resize", updatePopoverLayout);
    };
  }, [closeMenu, open, updatePopoverLayout]);

  useEffect(() => {
    const optionsChanged = !areSameValues(previousOptionsRef.current, options);
    const selectionChanged = !areSameValues(
      previousSelectedRef.current,
      selectedValues,
    );

    previousOptionsRef.current = options;
    previousSelectedRef.current = selectedValues;

    if (open && (optionsChanged || selectionChanged)) {
      setDraftValues(expandSelectedValues(options, selectedValues));
      setDraftSelectionChanged(false);
      setSearchQuery("");
    }
  }, [open, options, selectedValues]);

  const openMenu = () => {
    updatePopoverLayout();
    setDraftValues(expandSelectedValues(options, selectedValues));
    setDraftSelectionChanged(false);
    setSearchQuery("");
    setOpen(true);
  };

  const toggleOption = (option: string) => {
    setDraftSelectionChanged(true);
    setDraftValues((previous) => {
      const next = new Set(previous ?? []);
      if (next.has(option)) {
        next.delete(option);
      } else {
        next.add(option);
      }
      return options.filter((value) => next.has(value));
    });
  };

  const toggleVisibleOptions = () => {
    setDraftSelectionChanged(true);
    setDraftValues((previous) => {
      const next = new Set(previous ?? []);
      visibleOptions.forEach((option) => {
        if (allVisibleOptionsSelected) {
          next.delete(option);
        } else {
          next.add(option);
        }
      });
      return options.filter((value) => next.has(value));
    });
  };

  const invertVisibleOptions = () => {
    setDraftSelectionChanged(true);
    setDraftValues((previous) => {
      const next = new Set(previous ?? []);
      visibleOptions.forEach((option) => {
        if (next.has(option)) {
          next.delete(option);
        } else {
          next.add(option);
        }
      });
      return options.filter((value) => next.has(value));
    });
  };

  const applyDraft = () => {
    if (valuesToApply.length === 0) {
      return;
    }

    onChange(valuesToApply.length === options.length ? [] : valuesToApply);
    closeMenu(true);
  };

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
          onClick={() => {
            if (open) {
              closeMenu();
            } else {
              openMenu();
            }
          }}
          className={clsx(
            "ui-select-shell w-full",
            disabled && "cursor-not-allowed opacity-60",
            open &&
              "border-primary/45 bg-white shadow-lg shadow-stone-300/25 ring-2 ring-primary/10",
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
          <div
            style={{ maxHeight: popoverMaxHeight }}
            className={clsx(
              "absolute left-0 z-40 flex w-full flex-col overflow-hidden rounded-2xl border border-stone-200/90 bg-white shadow-[0_22px_40px_-26px_rgba(41,37,36,0.34)]",
              openUpward
                ? "bottom-[calc(100%+0.45rem)]"
                : "top-[calc(100%+0.45rem)]",
            )}
          >
            <div className="flex shrink-0 items-center justify-between gap-3 border-b border-stone-100 px-3 py-2">
              <span aria-live="polite" className="text-xs font-medium text-stone-500">
                {visibleOptions.length} 项 · 已选 {valuesToApply.length} 项
              </span>
              <button
                type="button"
                aria-label={`清除${label}筛选`}
                onClick={() => {
                  setDraftSelectionChanged(true);
                  setDraftValues([...options]);
                }}
                disabled={allOptionsSelected}
                className="rounded-lg px-2 py-1 text-xs font-medium text-stone-500 transition hover:bg-stone-100 hover:text-stone-800 disabled:cursor-default disabled:opacity-40"
              >
                清除筛选
              </button>
            </div>

            <div className="shrink-0 p-2">
              <div className="flex h-9 items-center gap-2 rounded-xl border border-stone-200 bg-stone-50 px-3 text-sm text-stone-600 focus-within:border-primary/40 focus-within:bg-white focus-within:ring-2 focus-within:ring-primary/10">
                <Search className="h-4 w-4 shrink-0 text-stone-400" />
                <input
                  ref={searchInputRef}
                  value={searchQuery}
                  aria-label={`搜索${label}选项`}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (
                      event.key !== "Enter" ||
                      event.nativeEvent.isComposing
                    ) {
                      return;
                    }

                    event.preventDefault();
                    applyDraft();
                  }}
                  placeholder={`搜索${label}`}
                  className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-stone-400"
                />
                {searchQuery ? (
                  <button
                    type="button"
                    aria-label={`清空${label}选项搜索`}
                    onClick={() => {
                      setSearchQuery("");
                      searchInputRef.current?.focus();
                    }}
                    className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg text-stone-400 transition hover:bg-stone-200/70 hover:text-stone-700"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                ) : null}
              </div>
            </div>

            <div className="grid shrink-0 grid-cols-2 gap-1 border-b border-stone-100 px-2 pb-2">
              <button
                type="button"
                aria-pressed={allVisibleOptionsSelected}
                onClick={toggleVisibleOptions}
                disabled={visibleOptions.length === 0}
                className="rounded-xl px-2 py-1.5 text-xs font-medium text-stone-600 transition hover:bg-stone-100 hover:text-stone-900 disabled:cursor-default disabled:opacity-40"
              >
                {allVisibleOptionsSelected
                  ? "取消全选"
                  : "全选当前结果"}
              </button>
              <button
                type="button"
                onClick={invertVisibleOptions}
                disabled={visibleOptions.length === 0}
                className="rounded-xl px-2 py-1.5 text-xs font-medium text-stone-600 transition hover:bg-stone-100 hover:text-stone-900 disabled:cursor-default disabled:opacity-40"
              >
                反选
              </button>
            </div>

            <div
              id={listboxId}
              role="listbox"
              aria-label={label}
              aria-multiselectable="true"
              className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto p-1"
            >
              {options.length === 0 ? (
                <div className="px-3 py-3 text-sm text-stone-400">暂无选项</div>
              ) : visibleOptions.length === 0 ? (
                <div className="px-3 py-3 text-sm text-stone-400">
                  没有匹配的{label}选项
                </div>
              ) : (
                visibleOptions.map((option) => {
                  const selected = draftSet.has(option);
                  return (
                    <button
                      key={option}
                      type="button"
                      role="option"
                      aria-selected={selected}
                      onClick={() => toggleOption(option)}
                      className={clsx(
                        "flex min-h-9 w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-[13px] leading-5 transition",
                        selected
                          ? "bg-primary/5 text-stone-900 hover:bg-primary/10"
                          : "text-stone-600 hover:bg-stone-100/90 hover:text-stone-900",
                      )}
                    >
                      {selected ? (
                        <SquareCheck className="h-4 w-4 shrink-0 text-primary" />
                      ) : (
                        <Square className="h-4 w-4 shrink-0 text-stone-400" />
                      )}
                      <span className="min-w-0 flex-1 truncate">
                        {optionLabels[option] ?? option}
                      </span>
                    </button>
                  );
                })
              )}
            </div>

            <div className="flex min-h-12 shrink-0 items-center justify-between gap-3 border-t border-stone-100 px-3 py-2">
              <span className="text-xs text-rose-600">
                {valuesToApply.length === 0 ? "至少保留一项" : ""}
              </span>
              <div className="flex shrink-0 items-center gap-2">
                <button
                  type="button"
                  onClick={() => closeMenu(true)}
                  className="ui-btn-secondary min-h-8 px-3 py-1 text-xs"
                >
                  <X className="h-3.5 w-3.5" />
                  取消
                </button>
                <button
                  type="button"
                  onClick={applyDraft}
                  disabled={valuesToApply.length === 0}
                  className="ui-btn-primary min-h-8 px-3 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-45"
                >
                  <Check className="h-3.5 w-3.5" />
                  应用
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
};
