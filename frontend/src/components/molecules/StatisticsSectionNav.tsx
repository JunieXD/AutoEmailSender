import clsx from "clsx";
import { Activity, CircleDot, GraduationCap, Mail, type LucideIcon } from "lucide-react";
import { useState, type CSSProperties } from "react";

export type StatisticsSectionNavItem = {
  id: string;
  label: string;
};

type StatisticsSectionNavProps = {
  items: StatisticsSectionNavItem[];
  activeSectionId: string;
  onSelect: (sectionId: string) => void;
  className?: string;
  style?: CSSProperties;
};

const sectionIconMap: Record<string, LucideIcon> = {
  mentor: GraduationCap,
  email: Mail,
  token: Activity,
};

const getSectionIcon = (item: StatisticsSectionNavItem) => {
  return sectionIconMap[item.id] ?? CircleDot;
};

export const StatisticsSectionNav = ({
  items,
  activeSectionId,
  onSelect,
  className,
  style,
}: StatisticsSectionNavProps) => {
  const [hoveredSectionId, setHoveredSectionId] = useState<string | null>(null);

  return (
    <nav
      aria-label="统计面板目录"
      data-testid="statistics-section-nav"
      style={style}
      className={clsx(
        "lg:fixed lg:top-[var(--statistics-section-nav-top,10rem)] lg:bottom-10 lg:z-30 lg:transition-[top] lg:duration-200 lg:ease-out",
        className,
      )}
    >
      <div className="relative mx-auto flex h-14 w-full items-center justify-center px-6 lg:h-full lg:w-[4.75rem] lg:px-0">
        <div
          data-testid="section-nav-frame"
          className="relative flex w-full items-center justify-between overflow-hidden rounded-2xl border border-white/75 bg-[linear-gradient(180deg,rgba(255,255,255,0.82),rgba(250,250,249,0.56))] px-8 shadow-[0_24px_58px_-34px_rgba(41,37,36,0.62),0_1px_0_rgba(255,255,255,0.78),inset_0_1px_0_rgba(255,255,255,0.96)] ring-1 ring-stone-900/5 backdrop-blur-xl lg:h-full lg:w-12 lg:flex-col lg:px-0 lg:py-7"
        >
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-px rounded-[1.15rem] bg-[radial-gradient(circle_at_50%_0%,rgba(255,255,255,0.86),transparent_54%),linear-gradient(180deg,rgba(153,27,27,0.045),transparent_30%,rgba(120,113,108,0.045))]"
          />
          {items.map((item) => {
            const active = item.id === activeSectionId;
            const hovered = item.id === hoveredSectionId;
            const Icon = getSectionIcon(item);

            return (
              <button
                key={item.id}
                type="button"
                aria-label={item.label}
                aria-current={active ? "true" : "false"}
                onClick={() => onSelect(item.id)}
                onMouseEnter={() => setHoveredSectionId(item.id)}
                onMouseLeave={() => setHoveredSectionId((current) => (current === item.id ? null : current))}
                onFocus={() => setHoveredSectionId(item.id)}
                onBlur={() => setHoveredSectionId((current) => (current === item.id ? null : current))}
                className="group relative z-10 flex h-11 w-11 cursor-pointer items-center justify-center rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20 focus-visible:ring-offset-2"
              >
                <span
                  aria-hidden="true"
                  data-testid={`section-nav-node-${item.id}`}
                  className={clsx(
                    "flex h-9 w-9 items-center justify-center rounded-xl border transition-all duration-200",
                    active
                      ? "border-primary/20 bg-[linear-gradient(180deg,rgba(254,242,242,0.98),rgba(255,255,255,0.88))] text-primary shadow-[0_0_0_5px_rgba(153,27,27,0.10),0_12px_24px_-18px_rgba(153,27,27,0.65),inset_0_1px_0_rgba(255,255,255,0.92)]"
                      : "border-white/80 bg-[linear-gradient(180deg,rgba(255,255,255,0.96),rgba(250,250,249,0.82))] text-stone-500 shadow-[0_10px_22px_-18px_rgba(41,37,36,0.55),inset_0_1px_0_rgba(255,255,255,0.96)] ring-1 ring-stone-900/5 group-hover:border-white group-hover:bg-[linear-gradient(180deg,rgba(255,255,255,1),rgba(245,245,244,0.9))] group-hover:text-stone-800 group-hover:shadow-[0_14px_26px_-18px_rgba(41,37,36,0.62),inset_0_1px_0_rgba(255,255,255,0.98)]",
                  )}
                >
                  <Icon
                    aria-hidden="true"
                    data-testid={`section-nav-icon-${item.id}`}
                    className="h-4 w-4 stroke-[1.8]"
                  />
                </span>
                {hovered ? (
                  <div
                    role="tooltip"
                    className="pointer-events-none absolute left-1/2 top-full z-20 mt-2 min-w-[8rem] -translate-x-1/2 rounded-xl border border-stone-200/90 bg-white/95 px-3 py-2.5 text-left text-xs text-stone-700 shadow-[0_18px_42px_-22px_rgba(28,25,23,0.45),0_0_0_1px_rgba(255,255,255,0.9)] backdrop-blur lg:left-full lg:top-1/2 lg:mt-0 lg:ml-4 lg:min-w-[9rem] lg:-translate-y-1/2 lg:translate-x-0"
                  >
                    <div className="flex items-center gap-2">
                      <span
                        aria-hidden="true"
                        className={clsx(
                          "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg",
                          active ? "bg-primary/10 text-primary" : "bg-stone-100 text-stone-600",
                        )}
                      >
                        <Icon className="h-4 w-4 stroke-[1.8]" />
                      </span>
                      <span>
                        <span className="block font-semibold text-stone-900">{item.label}</span>
                        <span className="mt-0.5 block text-stone-500">{active ? "当前版块" : "点击跳转"}</span>
                      </span>
                    </div>
                  </div>
                ) : null}
              </button>
            );
          })}
        </div>
      </div>
    </nav>
  );
};
