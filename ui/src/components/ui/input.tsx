import { Input as InputPrimitive } from "@base-ui/react/input";
import { cn } from "cn";
import type * as React from "react";

// docs/ui.md: input areas sit on --muted and show focus as a brand-coloured inner line.
const FIELD =
  "w-full min-w-0 rounded-md bg-muted px-3.5 outline-none placeholder:text-faint focus:ring-[1.5px] focus:ring-primary focus:ring-inset aria-invalid:ring-[1.5px] aria-invalid:ring-destructive aria-invalid:ring-inset";

function Input({ className, ...props }: React.ComponentProps<"input">) {
  return <InputPrimitive data-slot="input" className={cn("h-10", FIELD, className)} {...props} />;
}

export { FIELD, Input };
