import clsx from "clsx";
import { Check } from "lucide-react";

type SelectionToggleButtonProps = {
  label: string;
  selected: boolean;
  disabled?: boolean;
  onToggle: () => void;
};

export const SelectionToggleButton = ({
  label,
  selected,
  disabled = false,
  onToggle,
}: SelectionToggleButtonProps) => (
  <button
    type="button"
    aria-label={label}
    aria-pressed={selected}
    disabled={disabled}
    onClick={onToggle}
    className={clsx(
      "selection-toggle-button flex h-6 w-6 shrink-0 items-center justify-center rounded-lg border text-sm transition",
      "focus:outline-none focus:ring-2 focus:ring-primary/30 focus:ring-offset-2",
      selected
        ? "border-primary bg-primary text-white shadow-sm shadow-primary/20"
        : "border-stone-200 bg-white text-stone-300 hover:border-primary/40 hover:bg-primary/5 hover:text-primary",
      disabled ? "cursor-not-allowed opacity-45" : "cursor-pointer",
    )}
  >
    <Check
      className={clsx("h-3.5 w-3.5", selected ? "opacity-100" : "opacity-0")}
    />
  </button>
);
