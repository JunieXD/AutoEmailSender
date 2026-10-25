import { cn } from "cn";
import { useState } from "react";

export type Series = { name: string; unit: string; color: string };

const BOTH = [0, 1] as const;
export type Point = { label: string; title: string; values: readonly [number, number] };

/**
 * Grouped columns, one group per point, drawn with plain elements so they stay sharp at any width.
 * The numbers stay readable without colour: the tooltip and the table view name each series. A mouse
 * shows a point's numbers on hover, a finger or pen on tap.
 */
export function BarChart({
  series,
  points,
  caption,
}: {
  series: readonly [Series, Series];
  points: Point[];
  caption: string;
}) {
  const [active, setActive] = useState<number | null>(null);
  const ticks = niceTicks(Math.max(0, ...points.flatMap((p) => p.values)));
  const top = Math.max(...ticks);
  const every = Math.ceil(points.length / 7);
  const point = active === null ? undefined : points[active];
  return (
    <div className="relative" onPointerLeave={(e) => e.pointerType === "mouse" && setActive(null)}>
      <div className="relative ml-9 h-44" role="img" aria-label={caption}>
        {ticks.map((t) => (
          <div
            key={t}
            className="absolute inset-x-0 border-t border-border"
            style={{ bottom: `${top ? (t / top) * 100 : 0}%` }}
          >
            <span className="absolute -top-2 -left-9 w-7 text-right text-[11px] leading-4 text-faint tabular-nums">
              {t}
            </span>
          </div>
        ))}
        <div className="absolute inset-0 flex items-end gap-0.5">
          {points.map((p, i) => (
            <div
              // Columns are told apart by place alone; labels repeat across years.
              key={i}
              className={cn("flex h-full flex-1 items-end justify-center gap-px", active === i && "bg-muted")}
              onPointerEnter={(e) => e.pointerType === "mouse" && setActive(i)}
              onPointerUp={(e) => e.pointerType !== "mouse" && setActive(active === i ? null : i)}
            >
              {BOTH.map((n) => (
                <div
                  key={n}
                  className="w-full max-w-2.5 rounded-t-[3px]"
                  style={{ height: `${top ? (p.values[n] / top) * 100 : 0}%`, background: series[n].color }}
                />
              ))}
            </div>
          ))}
        </div>
        {point && active !== null && (
          <div
            className={cn(
              "pointer-events-none absolute bottom-full z-10 mb-2 rounded-lg bg-background px-3 py-2 text-xs whitespace-nowrap shadow-[0_4px_16px_var(--pop-shadow)] ring-1 ring-border",
              active < points.length / 4
                ? ""
                : active > (points.length * 3) / 4
                  ? "-translate-x-full"
                  : "-translate-x-1/2",
            )}
            style={{ left: `${((active + 0.5) / points.length) * 100}%` }}
          >
            <p className="mb-1 font-medium">{point.title}</p>
            {BOTH.map((n) => (
              <p key={n} className="flex items-center gap-1.5 text-muted-foreground">
                <span className="size-2 rounded-full" style={{ background: series[n].color }} />
                {series[n].name} <span className="text-foreground tabular-nums">{point.values[n]}</span>{" "}
                {series[n].unit}
              </p>
            ))}
          </div>
        )}
      </div>
      <div className="relative mt-1.5 ml-9 h-4 text-[11px] text-faint">
        {points.map((p, i) => {
          if (i % every !== 0) return null;
          // Under its column, slid along by as much of its width as the column is along the axis, so
          // the first and last labels stay within the chart.
          const along = ((i + 0.5) / points.length) * 100;
          return (
            <span
              key={i}
              className={cn("absolute whitespace-nowrap", (i / every) % 2 === 1 && "phone:hidden")}
              style={{ left: `${along}%`, transform: `translateX(-${along}%)` }}
            >
              {p.label}
            </span>
          );
        })}
      </div>
    </div>
  );
}

export function Legend({ series }: { series: readonly Series[] }) {
  return (
    <div className="flex items-center gap-3 text-xs text-muted-foreground">
      {series.map((s) => (
        <span key={s.name} className="flex items-center gap-1.5">
          <span className="size-2 rounded-full" style={{ background: s.color }} />
          {s.name}
        </span>
      ))}
    </div>
  );
}

/** Gridline values from 0: three or four steps of 1, 2 or 5 times a power of ten, covering `max`. */
export function niceTicks(max: number): number[] {
  if (max <= 0) return [0];
  const rough = max / 4;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= rough) ?? 10 * power;
  const whole = Math.max(1, Math.round(step));
  const ticks = [0];
  for (let t = whole; t - whole < max; t += whole) ticks.push(t);
  return ticks;
}
