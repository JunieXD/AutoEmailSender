import { X } from "lucide-react";
import type { ReactElement, ReactNode } from "react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

/**
 * docs/ui.md#交互: floats at the bottom middle of the table area while mentors are ticked, rising in
 * when it appears. The area under it needs room to scroll the last rows clear of it. Clearing does not
 * forget an action still running: its error shows here when it fails.
 */
export function BatchBar({
  count,
  errors,
  onClear,
  children,
}: {
  count: number;
  errors: string[];
  onClear: () => void;
  children: ReactNode;
}) {
  // An error outlasts the ticks, which may be cleared while the action is still running.
  if (count === 0 && errors.length === 0) return null;
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-4 z-20 flex justify-center px-4">
      <div className="pointer-events-auto max-w-full rounded-lg bg-background shadow-pop transition-[opacity,translate] duration-200 starting:translate-y-3 starting:opacity-0">
        {errors.map((e) => (
          <p key={e} className="px-4 pt-2.5 text-sm text-destructive">
            {e}
          </p>
        ))}
        <div className="flex items-center gap-1 overflow-x-auto p-1.5 [scrollbar-width:none]">
          <Button variant="ghost" size="icon" aria-label="取消选择" onClick={onClear}>
            <X strokeWidth={1.6} />
          </Button>
          {count > 0 && (
            <>
              <span className="pr-2 text-sm whitespace-nowrap">已选 {count} 位</span>
              {children}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/** Asks once more before a destructive action, saying what will happen (docs/ui.md#交互). */
export function Confirm({
  trigger,
  message,
  action,
  side = "top",
  onConfirm,
}: {
  trigger: ReactElement;
  message: string;
  action: string;
  side?: "top" | "bottom";
  onConfirm: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger render={trigger} />
      <PopoverContent side={side} align="end" className="w-80 p-3">
        <p>{message}</p>
        <div className="mt-3 flex justify-end gap-2">
          <Button variant="quiet" onClick={() => setOpen(false)}>
            取消
          </Button>
          <Button
            onClick={() => {
              setOpen(false);
              onConfirm();
            }}
          >
            {action}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
