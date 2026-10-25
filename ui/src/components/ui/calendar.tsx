import type { ComponentProps } from "react";
import { DayPicker, type ChevronProps } from "react-day-picker";
import { zhCN } from "react-day-picker/locale";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "cn";

/** shadcn calendar, with only the day selections used by sending. */
export function Calendar({ className, ...props }: ComponentProps<typeof DayPicker>) {
  return (
    <DayPicker
      locale={zhCN}
      showOutsideDays
      className={cn("relative w-fit max-w-full rounded-lg bg-muted p-2 text-sm", className)}
      classNames={{
        months: "relative",
        month_caption: "flex h-10 items-center justify-center font-medium",
        nav: "absolute inset-x-0 top-0 flex justify-between",
        button_previous: "grid size-10 place-items-center rounded-md hover:bg-press disabled:opacity-40",
        button_next: "grid size-10 place-items-center rounded-md hover:bg-press disabled:opacity-40",
        month_grid: "w-full border-collapse",
        weekday: "h-8 text-xs font-normal text-muted-foreground",
        day: "p-0 text-center",
        day_button: "size-10 rounded-md hover:bg-press focus-visible:outline-2 focus-visible:outline-ring",
        selected: "[&>button]:bg-primary [&>button]:text-primary-foreground",
        today: "font-semibold",
        outside: "text-faint",
        disabled: "text-faint opacity-40",
        hidden: "invisible",
      }}
      components={{
        Chevron: CalendarChevron,
      }}
      {...props}
    />
  );
}

function CalendarChevron({ orientation }: ChevronProps) {
  return orientation === "left" ? <ChevronLeft className="size-4" /> : <ChevronRight className="size-4" />;
}
