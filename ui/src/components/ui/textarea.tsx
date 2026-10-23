import { cn } from "cn";
import type * as React from "react";
import { FIELD } from "@/components/ui/input";

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn("block min-h-16 resize-y py-2.5 leading-relaxed", FIELD, className)}
      {...props}
    />
  );
}

export { Textarea };
