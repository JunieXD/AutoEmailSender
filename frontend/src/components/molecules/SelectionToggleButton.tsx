import clsx from "clsx";
import { Check } from "lucide-react";

type SelectionToggleButtonProps = {
  label: string;
  selected: boolean;
  disabled?: boolean;
  size?: "xs" | "sm" | "md" | "lg";
  shape?: "square" | "circle";
  semantics?: "toggle-button" | "checkbox";
  className?: string;
  onToggle: () => void;
};

const sizeClasses = {
  xs: "h-4 w-4",
  sm: "h-[1.125rem] w-[1.125rem]",
  md: "h-5 w-5",
  lg: "h-6 w-6",
} as const;

const iconSizeClasses = {
  xs: "h-2.5 w-2.5",
  sm: "h-3 w-3",
  md: "h-3.5 w-3.5",
  lg: "h-3.5 w-3.5",
} as const;

const squareRadiusClasses = {
  xs: "rounded",
  sm: "rounded-md",
  md: "rounded-md",
  lg: "rounded-lg",
} as const;

export const SelectionToggleButton = ({
  label,
  selected,
  disabled = false,
  size = "lg",
  shape = "square",
  semantics = "toggle-button",
  className,
  onToggle,
}: SelectionToggleButtonProps) => (
  <button
    type="button"
    role={semantics === "checkbox" ? "checkbox" : undefined}
    aria-label={label}
    aria-checked={semantics === "checkbox" ? selected : undefined}
    aria-pressed={semantics === "toggle-button" ? selected : undefined}
    disabled={disabled}
    onClick={onToggle}
    className={clsx(
      "selection-toggle-button flex shrink-0 items-center justify-center border text-sm transition",
      sizeClasses[size],
      shape === "circle" ? "rounded-full" : squareRadiusClasses[size],
      "focus:outline-none focus:ring-2 focus:ring-primary/30 focus:ring-offset-2",
      selected
        ? "border-primary bg-primary text-white shadow-sm shadow-primary/20"
        : "border-stone-200 bg-white text-stone-300 hover:border-primary/40 hover:bg-primary/5 hover:text-primary",
      disabled ? "cursor-not-allowed opacity-45" : "cursor-pointer",
      className,
    )}
  >
    <Check
      className={clsx(
        iconSizeClasses[size],
        selected ? "opacity-100" : "opacity-0",
      )}
    />
  </button>
);
