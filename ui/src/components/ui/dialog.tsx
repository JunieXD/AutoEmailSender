import { Dialog as DialogPrimitive } from "@base-ui/react/dialog";
import { cn } from "cn";
import { XIcon } from "lucide-react";
import type * as React from "react";
import { Button } from "@/components/ui/button";

// docs/ui.md#尺寸 and #动效: a 20px-cornered card that fades in; on a phone it fills the screen.

function Dialog({ ...props }: DialogPrimitive.Root.Props) {
  return <DialogPrimitive.Root data-slot="dialog" {...props} />;
}

function DialogContent({ className, children, ...props }: DialogPrimitive.Popup.Props) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-[rgb(20_17_16/0.4)] transition-opacity duration-150 data-ending-style:opacity-0 data-starting-style:opacity-0" />
      <div className="fixed inset-0 z-50 grid grid-cols-[minmax(0,1fr)] place-items-center p-6 phone:p-0">
        <DialogPrimitive.Popup
          data-slot="dialog-content"
          className={cn(
            "flex max-h-[calc(100dvh-3rem)] min-h-0 w-[660px] max-w-full min-w-0 flex-col rounded-xl bg-background shadow-pop transition-opacity duration-150 outline-none data-ending-style:opacity-0 data-starting-style:opacity-0 phone:h-full phone:max-h-dvh phone:w-full phone:rounded-none",
            className,
          )}
          {...props}
        >
          {children}
        </DialogPrimitive.Popup>
      </div>
    </DialogPrimitive.Portal>
  );
}

function DialogHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 pt-[18px] pr-[18px] pb-1.5 pl-6 phone:pl-4">
      <DialogPrimitive.Title className="grow text-xl font-semibold">{children}</DialogPrimitive.Title>
      <DialogPrimitive.Close render={<Button variant="ghost" size="icon" aria-label="关闭" />}>
        <XIcon strokeWidth={1.6} />
      </DialogPrimitive.Close>
    </div>
  );
}

function DialogBody({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("flex-1 overflow-y-auto px-6 py-2 phone:px-4", className)} {...props} />;
}

function DialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("flex items-center gap-2 px-6 pt-3.5 pb-5 phone:px-4", className)} {...props} />;
}

const DialogClose = DialogPrimitive.Close;

export { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader };
