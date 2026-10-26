import type { ReactNode } from "react";
import { cn } from "cn";

/** Pills that pick one of a few values, such as a list's filter or a time range. */
export function Segments<T>({
  label,
  value,
  options,
  onChange,
  disabled,
  className,
}: {
  label: string;
  value: T;
  // The type comes from `value`, so options written in place keep their literal types.
  options: readonly (readonly [NoInfer<T>, ReactNode])[];
  onChange: (value: NoInfer<T>) => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div
      role="tablist"
      aria-label={label}
      className={cn("inline-flex gap-0.5 rounded-full bg-muted p-[3px]", className)}
    >
      {options.map(([option, text], i) => (
        <button
          // The options are a fixed list.
          key={i}
          role="tab"
          aria-selected={value === option}
          disabled={disabled}
          onClick={() => onChange(option)}
          className="flex h-[30px] items-center gap-1.5 rounded-full px-3.5 text-sm whitespace-nowrap text-muted-foreground aria-selected:bg-background aria-selected:font-medium aria-selected:text-foreground aria-selected:shadow-sm phone:h-10 dark:aria-selected:bg-input"
        >
          {text}
        </button>
      ))}
    </div>
  );
}
