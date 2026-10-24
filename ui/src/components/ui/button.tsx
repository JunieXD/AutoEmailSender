import { Button as ButtonPrimitive } from "@base-ui/react/button";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "cn";

// docs/ui.md#尺寸: 12px corners, 18px icons. Focus uses the global outline in index.css.
const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md font-medium whitespace-nowrap transition-colors select-none disabled:pointer-events-none disabled:opacity-40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-[18px]",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary-hover",
        quiet: "bg-muted hover:bg-press",
        ghost: "text-muted-foreground hover:bg-muted hover:text-foreground",
        // docs/ui.md#颜色: what AI does stands out in the brand's colours.
        ai: "bg-selected text-brand-text hover:bg-[color-mix(in_srgb,var(--selected)_88%,var(--primary))]",
      },
      size: {
        default: "h-[38px] px-4",
        sm: "h-7 rounded-full px-[11px] text-[12.5px] font-normal",
        icon: "size-[34px]",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonPrimitive.Props & VariantProps<typeof buttonVariants>) {
  return <ButtonPrimitive data-slot="button" className={cn(buttonVariants({ variant, size, className }))} {...props} />;
}

export { Button, buttonVariants };
