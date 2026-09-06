import { type EditorId } from "@/features/profile-setup/model/profileForms";
import clsx from "clsx";
import { Plus } from "lucide-react";

export type EditorOption = {
  id: number;
  name: string;
  is_default: boolean;
};

export type EditorSwitcherProps = {
  label: string;
  helper?: string;
  options: EditorOption[];
  activeId: EditorId;
  createLabel: string;
  creatingLabel: string;
  onCreate: () => void;
  onSelect: (id: number) => void;
};

export const EditorSwitcher = ({
  label,
  helper,
  options,
  activeId,
  createLabel,
  creatingLabel,
  onCreate,
  onSelect,
}: EditorSwitcherProps) => (
  <div className="rounded-2xl border border-stone-200 bg-white px-4 py-4 shadow-sm shadow-stone-100/60">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <div className="text-sm font-medium text-stone-900">{label}</div>
        {helper ? (
          <p className="mt-1 text-xs leading-5 text-stone-500">{helper}</p>
        ) : null}
      </div>
      {options.length > 0 ? (
        <button
          type="button"
          onClick={onCreate}
          className="inline-flex items-center gap-2 rounded-xl border border-stone-200 bg-stone-50 px-3 py-2 text-sm font-medium text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-900"
        >
          <Plus className="h-4 w-4" />
          {createLabel}
        </button>
      ) : null}
    </div>

    <div className="mt-4 flex flex-wrap gap-2">
      {options.length === 0 ? (
        <div className="w-full rounded-2xl border border-dashed border-primary/20 bg-primary/5 px-4 py-4">
          <div className="text-sm font-medium text-primary">
            {creatingLabel}
          </div>
        </div>
      ) : (
        options.map((option) => {
          const isActive = activeId === option.id;
          return (
            <button
              key={option.id}
              type="button"
              onClick={() => onSelect(option.id)}
              className={clsx(
                "inline-flex items-center gap-2 rounded-2xl border px-4 py-3 text-sm font-medium transition-all",
                isActive
                  ? "border-primary/20 bg-primary text-white shadow-sm shadow-primary/20"
                  : "border-stone-200 bg-stone-50 text-stone-700 hover:border-stone-300 hover:bg-white hover:text-stone-900",
              )}
            >
              <span>{option.name}</span>
              {option.is_default && (
                <span
                  className={clsx(
                    "rounded-full px-2 py-0.5 text-[11px]",
                    isActive
                      ? "bg-white/18 text-white"
                      : "bg-white text-stone-500",
                  )}
                >
                  默认
                </span>
              )}
            </button>
          );
        })
      )}

      {options.length > 0 && activeId === "new" && (
        <div className="inline-flex items-center rounded-2xl border border-primary/20 bg-primary/5 px-4 py-3 text-sm font-medium text-primary">
          {creatingLabel}
        </div>
      )}
    </div>
  </div>
);
