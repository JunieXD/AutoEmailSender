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
) => {
  const detail =
    selectedValues.length === 0
      ? "未选择字段"
      : selectedValues.length === options.length
        ? "全部字段"
        : selectedValues.length === 1
          ? "已选一项"
          : `已选 ${selectedValues.length} 项`;

  return `选择字段：${detail}`;
};

const areSameValues = <TValue extends string>(
  previousValues: TValue[],
  nextValues: TValue[],
) =>
  previousValues.length === nextValues.length &&
  previousValues.every((value, index) => value === nextValues[index]);

export const KeywordSearchScopeSelect = <TValue extends string = string>({
  label,
  options,
  selectedValues,
  disabled = false,
  onChange,
}: KeywordSearchScopeSelectProps<TValue>) => {
  const [open, setOpen] = useState(false);
  // draftValues 为 null 表示非草稿态，使用 selectedValues 渲染；
  // 为数组（通常是空数组）则视觉上展示该数组，但 onChange 尚未触发。
  const [draftValues, setDraftValues] = useState<TValue[] | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const previousSelectedRef = useRef(selectedValues);
  const listboxId = useId();
  const isDraft = draftValues !== null;
  const displayValues = draftValues ?? selectedValues;
  const displaySet = new Set(displayValues);
  const summary = getSummary(options, displayValues);

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

  // 边界 1：关闭面板时丢弃草稿，恢复显示真实选择。
  useEffect(() => {
    if (!open) {
      setDraftValues(null);
    }
  }, [open]);

  // 边界 3：父组件 selectedValues 内容变化（如切换 identity 触发 sessionStorage 重读）时清草稿，
  // 避免临时态泄漏到新的上下文；等值新数组不应打断当前草稿。
  useEffect(() => {
    if (!areSameValues(previousSelectedRef.current, selectedValues)) {
      previousSelectedRef.current = selectedValues;
      setDraftValues(null);
    }
  }, [selectedValues]);

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
          <div className="flex items-center gap-1 border-b border-stone-100 py-1.5">
            <button
              type="button"
              onClick={() => {
                onChange(options.map((option) => option.value));
                setDraftValues(null);
              }}
              disabled={displayValues.length === options.length}
              className="flex flex-1 items-center justify-start rounded-xl px-3 py-1.5 text-left text-xs font-medium text-stone-600 transition hover:bg-stone-100 hover:text-stone-900 disabled:cursor-default disabled:opacity-50"
            >
              全部选择
            </button>
            <button
              type="button"
              onClick={() => setDraftValues([])}
              disabled={displayValues.length === 0}
              className="flex flex-1 items-center justify-start rounded-xl px-3 py-1.5 text-left text-xs font-medium text-stone-600 transition hover:bg-stone-100 hover:text-stone-900 disabled:cursor-default disabled:opacity-50"
            >
              全部取消
            </button>
          </div>
          <div
            id={listboxId}
            role="listbox"
            aria-label={label}
            aria-multiselectable="true"
            className="flex max-h-64 flex-col gap-1 overflow-y-auto py-1"
          >
            {options.map((option) => {
              const selected = displaySet.has(option.value);
              // 草稿态下不锁定，确保用户可以从空状态点选第一项；
              // 非草稿态保持原有"最后一项不可取消"的护栏。
              const locked =
                !isDraft && selected && selectedValues.length === 1;

              return (
                <button
                  key={option.value}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() => {
                    if (isDraft) {
                      // 草稿态首次点选即提交并退出草稿，简化时机管理。
                      onChange([option.value]);
                      setDraftValues(null);
                      return;
                    }

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
            {isDraft ? "选择一项以应用，关闭面板将恢复原选择" : "至少保留一项"}
          </div>
        </div>
      ) : null}
    </div>
  );
};
