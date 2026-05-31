import {
  Children,
  isValidElement,
  type ChangeEvent,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";
import { Check, ChevronDown } from "lucide-react";

type NativeSelectFieldProps = {
  label?: string;
  icon?: ReactNode;
  wrapperClassName?: string;
  shellClassName?: string;
  selectClassName?: string;
  menuPlacement?: "popover" | "inline" | "floating-up";
  children: ReactNode;
  id?: string;
  disabled?: boolean;
  value?: string | number | readonly string[];
  defaultValue?: string | number | readonly string[];
  name?: string;
  onChange?: (event: ChangeEvent<HTMLSelectElement>) => void;
  ariaLabel?: string;
};

type ParsedOption = {
  value: string;
  label: string;
  disabled: boolean;
};

const extractText = (node: ReactNode): string => {
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }

  return Children.toArray(node)
    .map((child) => extractText(child))
    .join("")
    .trim();
};

const parseOptions = (children: ReactNode): ParsedOption[] =>
  Children.toArray(children).flatMap((child) => {
    if (!isValidElement(child)) {
      return [];
    }
    if (child.type !== "option") {
      return [];
    }

    const option = child as React.ReactElement<{
      value?: string | number;
      children?: ReactNode;
      disabled?: boolean;
    }>;
    const valueProp = option.props.value;
    return [
      {
        value:
          typeof valueProp === "string" || typeof valueProp === "number"
            ? String(valueProp)
            : extractText(option.props.children),
        label: extractText(option.props.children),
        disabled: Boolean(option.props.disabled),
      },
    ];
  });

export const NativeSelectField = ({
  label,
  icon,
  wrapperClassName,
  shellClassName,
  selectClassName,
  menuPlacement = "popover",
  children,
  id,
  disabled,
  value,
  defaultValue,
  name,
  onChange,
  ariaLabel,
}: NativeSelectFieldProps) => {
  const [open, setOpen] = useState(false);
  const [floatingMenuStyle, setFloatingMenuStyle] = useState<CSSProperties>();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const menuId = useId();
  const options = useMemo(() => parseOptions(children), [children]);
  const currentValue = String(
    value ?? defaultValue ?? options.find((option) => !option.disabled)?.value ?? "",
  );
  const selectedOption =
    options.find((option) => option.value === currentValue) ?? options[0] ?? null;
  const shouldScroll = options.length > 6;

  useEffect(() => {
    if (!open) {
      return;
    }

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (
        !rootRef.current?.contains(target) &&
        !menuRef.current?.contains(target)
      ) {
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

  useLayoutEffect(() => {
    if (!open || menuPlacement !== "floating-up") {
      return;
    }

    const updateFloatingMenuStyle = () => {
      const triggerRect = triggerRef.current?.getBoundingClientRect();
      if (!triggerRect) {
        return;
      }

      const viewportPadding = 12;
      const gap = 8;
      const availableAbove = Math.max(
        120,
        triggerRect.top - viewportPadding - gap,
      );
      const menuWidth = Math.max(triggerRect.width, 176);
      const left = Math.min(
        Math.max(viewportPadding, triggerRect.left),
        Math.max(viewportPadding, window.innerWidth - menuWidth - viewportPadding),
      );

      setFloatingMenuStyle({
        left,
        bottom: Math.max(viewportPadding, window.innerHeight - triggerRect.top + gap),
        width: menuWidth,
        maxHeight: Math.min(240, availableAbove),
      });
    };

    updateFloatingMenuStyle();
    window.addEventListener("resize", updateFloatingMenuStyle);
    window.addEventListener("scroll", updateFloatingMenuStyle, true);
    return () => {
      window.removeEventListener("resize", updateFloatingMenuStyle);
      window.removeEventListener("scroll", updateFloatingMenuStyle, true);
    };
  }, [menuPlacement, open]);

  const emitChange = (nextValue: string) => {
    if (!onChange) {
      return;
    }

    const syntheticEvent = {
      target: { value: nextValue },
      currentTarget: { value: nextValue },
    } as ChangeEvent<HTMLSelectElement>;

    onChange(syntheticEvent);
  };

  const handleButtonKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (disabled) {
      return;
    }
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setOpen(true);
    }
  };

  const menu = open ? (
    <div
      id={menuId}
      ref={menuRef}
      role="listbox"
      style={menuPlacement === "floating-up" ? floatingMenuStyle : undefined}
      className={clsx(
        "z-40 overflow-hidden rounded-2xl border border-stone-200/90 bg-[linear-gradient(180deg,rgba(255,255,255,0.99),rgba(250,250,249,0.97))] p-1 shadow-[0_22px_40px_-26px_rgba(41,37,36,0.34)] backdrop-blur-xl",
        menuPlacement === "inline"
          ? "relative mt-2 w-full"
          : menuPlacement === "floating-up"
            ? "fixed"
            : "absolute left-0 top-[calc(100%+0.45rem)] w-full",
      )}
    >
      <div
        className={clsx(
          (shouldScroll || menuPlacement === "floating-up") &&
            "max-h-60 overflow-y-auto pr-0.5",
        )}
      >
        {options.map((option) => {
          const selected = option.value === currentValue;
          return (
            <button
              key={option.value}
              type="button"
              role="option"
              aria-selected={selected}
              disabled={option.disabled}
              onClick={() => {
                emitChange(option.value);
                setOpen(false);
                triggerRef.current?.blur();
              }}
              className={clsx(
                "flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2 text-left text-[13px] leading-5 transition",
                option.disabled
                  ? "cursor-not-allowed text-stone-300"
                  : selected
                    ? "bg-primary text-white shadow-sm shadow-primary/25"
                    : "text-stone-700 hover:bg-stone-100/90 hover:text-stone-900",
              )}
            >
              <span className="truncate">{option.label}</span>
              {selected ? <Check className="h-4 w-4 shrink-0" /> : null}
            </button>
          );
        })}
      </div>
    </div>
  ) : null;

  return (
    <label className={clsx("block", wrapperClassName)}>
      {label ? (
        <div className="mb-2 text-sm font-medium text-stone-800">{label}</div>
      ) : null}
      <div ref={rootRef} className="relative min-w-0">
        {name ? <input type="hidden" name={name} value={currentValue} /> : null}
        <button
          id={id}
          ref={triggerRef}
          type="button"
          disabled={disabled}
          aria-label={ariaLabel}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={menuId}
          onClick={() => setOpen((previous) => !previous)}
          onKeyDown={handleButtonKeyDown}
          className={clsx(
            "ui-select-shell w-full",
            disabled && "cursor-not-allowed opacity-60",
            open && "border-primary/45 bg-white shadow-lg shadow-stone-300/25 ring-2 ring-primary/10",
            shellClassName,
            selectClassName,
          )}
        >
          {icon ? (
            <span className="pointer-events-none shrink-0 text-primary">{icon}</span>
          ) : null}
          <span className="flex-1 truncate text-left text-sm text-stone-700">
            {selectedOption?.label ?? "请选择"}
          </span>
          <ChevronDown
            className={clsx(
              "ui-select-chevron",
              open && "rotate-180 text-primary",
            )}
          />
        </button>

        {menuPlacement === "floating-up" && menu
          ? createPortal(menu, document.body)
          : menu}
      </div>
    </label>
  );
};
