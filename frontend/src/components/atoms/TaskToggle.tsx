interface TaskToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
}

export const TaskToggle: React.FC<TaskToggleProps> = ({ checked, onChange, label }) => {
  return (
    <label className="inline-flex cursor-pointer items-center gap-3">
      <button
        type="button"
        role="switch"
        aria-label={label}
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`relative h-5 w-9 shrink-0 rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-primary/30 focus:ring-offset-2 ${
          checked ? "bg-primary" : "bg-stone-200"
        }`}
      >
        <span
          className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform ${
            checked ? "translate-x-4" : "translate-x-0"
          }`}
        />
      </button>
      <span className="text-sm text-stone-700">{label}</span>
    </label>
  );
};
