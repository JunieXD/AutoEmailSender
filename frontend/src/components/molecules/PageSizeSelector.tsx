import { useEffect, useId, useMemo, useState, type KeyboardEvent } from "react";
import { NativeSelectField } from "@/components/atoms/NativeSelectField";
import {
  MAX_PAGE_SIZE,
  MIN_PAGE_SIZE,
  PAGE_SIZE_OPTIONS,
} from "@/lib/pagination";

type PageSizeSelectorProps = {
  value: number;
  onChange: (pageSize: number) => void;
  className?: string;
  unitLabel?: string;
  menuPlacement?: "popover" | "inline" | "floating-up";
  options?: readonly number[];
  disabled?: boolean;
  ariaLabel?: string;
};

const CUSTOM_VALUE = "custom";

export const PageSizeSelector = ({
  value,
  onChange,
  className,
  unitLabel = "位",
  menuPlacement = "floating-up",
  options = PAGE_SIZE_OPTIONS,
  disabled = false,
  ariaLabel = "每页数量",
}: PageSizeSelectorProps) => {
  const fixedOptions = useMemo(
    () =>
      Array.from(
        new Set(
          options.filter(
            (option) =>
              Number.isInteger(option) &&
              option >= MIN_PAGE_SIZE &&
              option <= MAX_PAGE_SIZE,
          ),
        ),
      ),
    [options],
  );
  const valueMatchesFixedOption = fixedOptions.includes(value);
  const [customMode, setCustomMode] = useState(!valueMatchesFixedOption);
  const selectedValue =
    customMode || !valueMatchesFixedOption ? CUSTOM_VALUE : String(value);
  const [customValue, setCustomValue] = useState(String(value));
  const [customError, setCustomError] = useState<string | null>(null);
  const customErrorId = useId();

  useEffect(() => {
    setCustomValue(String(value));
    setCustomMode(!valueMatchesFixedOption);
    setCustomError(null);
  }, [value, valueMatchesFixedOption]);

  const applyCustomValue = () => {
    const trimmedValue = customValue.trim();
    if (trimmedValue === "") {
      setCustomError("请输入每页数量");
      return;
    }

    const numericValue = Number(trimmedValue);
    if (
      !Number.isInteger(numericValue) ||
      numericValue < MIN_PAGE_SIZE ||
      numericValue > MAX_PAGE_SIZE
    ) {
      setCustomError(
        `请输入 ${MIN_PAGE_SIZE}–${MAX_PAGE_SIZE} 之间的整数`,
      );
      return;
    }

    setCustomError(null);
    setCustomValue(String(numericValue));
    if (numericValue !== value) {
      onChange(numericValue);
    }
  };

  const handleCustomKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      applyCustomValue();
    } else if (event.key === "Escape") {
      setCustomValue(String(value));
      setCustomError(null);
    }
  };

  return (
    <div
      className={`${
        selectedValue === CUSTOM_VALUE
          ? "flex flex-wrap items-center gap-2"
          : "flex shrink-0 flex-nowrap items-center gap-2 whitespace-nowrap"
      } ${className ?? ""}`}
    >
      <span className="text-sm text-stone-500">每页</span>
      <NativeSelectField
        ariaLabel={ariaLabel}
        value={selectedValue}
        disabled={disabled}
        onChange={(event) => {
          const nextValue = event.target.value;
          if (nextValue === CUSTOM_VALUE) {
            setCustomMode(true);
            setCustomValue(String(value));
            setCustomError(null);
            return;
          }
          setCustomMode(false);
          setCustomError(null);
          const numericValue = Number(nextValue);
          if (numericValue !== value) {
            onChange(numericValue);
          }
        }}
        wrapperClassName="w-24"
        shellClassName="!min-h-0 h-9 rounded-2xl px-3 py-0 shadow-none"
        menuPlacement={menuPlacement}
      >
        {fixedOptions.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
        <option value={CUSTOM_VALUE}>自定义</option>
      </NativeSelectField>
      {selectedValue === CUSTOM_VALUE ? (
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="number"
            min={MIN_PAGE_SIZE}
            max={MAX_PAGE_SIZE}
            step={1}
            aria-label="自定义每页数量"
            aria-invalid={Boolean(customError)}
            aria-describedby={customError ? customErrorId : undefined}
            value={customValue}
            disabled={disabled}
            onChange={(event) => {
              setCustomValue(event.target.value);
              setCustomError(null);
            }}
            onKeyDown={handleCustomKeyDown}
            className="h-9 w-20 rounded-2xl border border-stone-200 bg-white px-3 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15 disabled:cursor-not-allowed disabled:opacity-60"
          />
          <button
            type="button"
            disabled={disabled}
            onClick={applyCustomValue}
            className="ui-btn-secondary h-9 px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-60"
          >
            应用
          </button>
          {customError ? (
            <span id={customErrorId} role="alert" className="text-xs text-red-600">
              {customError}
            </span>
          ) : null}
        </div>
      ) : null}
      <span className="text-sm text-stone-500">{unitLabel}</span>
    </div>
  );
};
