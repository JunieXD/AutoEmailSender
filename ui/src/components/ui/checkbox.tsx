import { Checkbox as CheckboxPrimitive } from "@base-ui/react/checkbox";
import { cn } from "cn";
import { CheckIcon, MinusIcon } from "lucide-react";

// docs/ui.md#尺寸: 16px with 5px corners, outlined in --input, filled with the brand colour when checked
// or partly checked.
function Checkbox({ className, ...props }: CheckboxPrimitive.Root.Props) {
  return (
    <CheckboxPrimitive.Root
      data-slot="checkbox"
      className={cn(
        "grid size-4 shrink-0 place-items-center rounded-[5px] border-[1.5px] border-input bg-background data-checked:border-primary data-checked:bg-primary data-checked:text-primary-foreground data-indeterminate:border-primary data-indeterminate:bg-primary data-indeterminate:text-primary-foreground",
        className,
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator className="[&>svg]:size-3">
        <CheckIcon strokeWidth={3} className="in-data-indeterminate:hidden" />
        <MinusIcon strokeWidth={3} className="hidden in-data-indeterminate:block" />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}

export { Checkbox };
