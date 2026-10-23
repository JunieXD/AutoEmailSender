import { cn } from "cn";
import { ChevronDownIcon } from "lucide-react";
import type * as React from "react";
import { FIELD } from "@/components/ui/input";

// docs/ui.md: styled like the text fields; the list itself is the system's.

function NativeSelect({ className, ...props }: React.ComponentProps<"select">) {
  return (
    <div
      className={cn("group/native-select relative has-[select:disabled]:opacity-40", className)}
      data-slot="native-select-wrapper"
    >
      <select data-slot="native-select" className={cn("h-10 appearance-none pr-9", FIELD)} {...props} />
      <ChevronDownIcon
        className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-muted-foreground"
        aria-hidden="true"
        data-slot="native-select-icon"
        strokeWidth={1.6}
      />
    </div>
  );
}

function NativeSelectOption({ className, ...props }: React.ComponentProps<"option">) {
  return (
    <option data-slot="native-select-option" className={cn("bg-[Canvas] text-[CanvasText]", className)} {...props} />
  );
}

export { NativeSelect, NativeSelectOption };
