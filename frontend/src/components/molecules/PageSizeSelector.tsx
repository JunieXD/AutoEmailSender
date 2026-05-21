import { useEffect, useState, type KeyboardEvent } from "react";
import { NativeSelectField } from "@/components/atoms/NativeSelectField";
import {
  clampPageSize,
  MAX_PAGE_SIZE,
  MIN_PAGE_SIZE,
  PAGE_SIZE_OPTIONS,
} from "@/lib/pagination";

type PageSizeSelectorProps = {
  value: number;
  onChange: (pageSize: number) => void;
  className?: string;
};

const CUSTOM_VALUE = "custom";

export const PageSizeSelector = ({
  value,
  onChange,
  className,
}: PageSizeSelectorProps) => {
  const valueMatchesFixedOption = PAGE_SIZE_OPTIONS.includes(
    value as (typeof PAGE_SIZE_OPTIONS)[number],
  );
  const [customMode, setCustomMode] = useState(!valueMatchesFixedOption);
  const selectedValue =
    customMode || !valueMatchesFixedOption ? CUSTOM_VALUE : String(value);
  const [customValue, setCustomValue] = useState(String(value));

  useEffect(() => {
    setCustomValue(String(value));
    setCustomMode(!valueMatchesFixedOption);
  }, [value, valueMatchesFixedOption]);

  const applyCustomValue = () => {
    if (customValue.trim() === "") {
      setCustomValue(String(value));
      return;
    }

    const numericValue = Number(customValue);
    if (!Number.isFinite(numericValue)) {
      setCustomValue(String(value));
      return;
    }
    const nextValue = clampPageSize(numericValue);
    setCustomValue(String(nextValue));
    onChange(nextValue);
  };

  const handleCustomKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      applyCustomValue();
    }
  };

  return (
    <div className={`flex flex-wrap items-center gap-2 ${className ?? ""}`}>
      <span className="text-sm text-stone-500">每页</span>
      <NativeSelectField
        ariaLabel="每页数量"
        value={selectedValue}
        onChange={(event) => {
          const nextValue = event.target.value;
          if (nextValue === CUSTOM_VALUE) {
            setCustomMode(true);
            setCustomValue(String(value));
            return;
          }
          setCustomMode(false);
          onChange(Number(nextValue));
        }}
        wrapperClassName="w-24"
        shellClassName="!min-h-0 h-9 rounded-2xl px-3 py-0 shadow-none"
        menuPlacement="floating-up"
      >
        {PAGE_SIZE_OPTIONS.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
        <option value={CUSTOM_VALUE}>自定义</option>
      </NativeSelectField>
      {selectedValue === CUSTOM_VALUE ? (
        <input
          type="number"
          min={MIN_PAGE_SIZE}
          max={MAX_PAGE_SIZE}
          aria-label="自定义每页数量"
          value={customValue}
          onChange={(event) => setCustomValue(event.target.value)}
          onBlur={applyCustomValue}
          onKeyDown={handleCustomKeyDown}
          className="h-9 w-20 rounded-2xl border border-stone-200 bg-white px-3 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15"
        />
      ) : null}
      <span className="text-sm text-stone-500">位</span>
    </div>
  );
};
