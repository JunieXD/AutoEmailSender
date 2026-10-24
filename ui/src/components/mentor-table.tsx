import { Link } from "@tanstack/react-router";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useEffectEvent } from "react";
import type { MatchSummary, MentorRow } from "@/api/types";
import { Avatar, ContactLabel, contactDetail, BASIS_CHANGED, Missing } from "@/components/mentor";
import { TagChip } from "@/components/tags";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";

const PAGE_SIZE = 50;

/** A page number from the URL; anything but a whole number above 1 means the first page. */
export function pageParam(page: unknown): number | undefined {
  return typeof page === "number" && Number.isInteger(page) && page > 1 ? page : undefined;
}

// docs/ui.md#布局: no tag column on narrow windows; each row is a card where the columns do not fit.
const COLUMNS =
  "grid grid-cols-[16px_minmax(150px,1.1fr)_minmax(170px,1.2fr)_minmax(180px,1.8fr)_110px_minmax(176px,1fr)_minmax(64px,0.5fr)] items-center gap-3 narrow:grid-cols-[16px_minmax(140px,1.1fr)_minmax(150px,1.2fr)_minmax(140px,1.4fr)_110px_minmax(170px,1fr)]";
const CARD =
  "cards:h-auto cards:grid-cols-[16px_minmax(0,1fr)_auto] cards:gap-x-3 cards:gap-y-1 cards:rounded-none cards:border-b cards:border-border cards:px-4 cards:py-3 cards:[grid-template-areas:'check_who_match'_'check_place_contact'_'check_research_research']";
const TAGS = "narrow:hidden";

/**
 * The mentor table of the list and the trash, with a tick box on each row (docs/ui.md#交互). The
 * boxes are off while the rows are the previous page or filter, kept on screen until the new ones
 * arrive, so nothing out of view gets ticked.
 */
export function MentorTable({
  mentors,
  selected,
  selectable,
  onSelect,
}: {
  mentors: MentorRow[];
  selected: ReadonlySet<number>;
  selectable: boolean;
  onSelect: (selected: ReadonlySet<number>) => void;
}) {
  const ticked = mentors.filter((m) => selected.has(m.id)).length;
  function tick(ids: number[], on: boolean) {
    const next = new Set(selected);
    for (const id of ids) {
      if (on) next.add(id);
      else next.delete(id);
    }
    onSelect(next);
  }
  return (
    <div
      role="table"
      aria-label="导师"
      className="flex flex-col gap-[3px] pb-4 cards:gap-0 cards:border-t cards:border-border cards:pb-0"
    >
      <div role="row" className={`${COLUMNS} px-3 pb-1 text-sm text-muted-foreground cards:hidden`}>
        <span role="columnheader" className="flex">
          <Checkbox
            aria-label="选择本页全部导师"
            disabled={!selectable}
            checked={mentors.length > 0 && ticked === mentors.length}
            indeterminate={ticked > 0 && ticked < mentors.length}
            onCheckedChange={(on) =>
              tick(
                mentors.map((m) => m.id),
                on,
              )
            }
          />
        </span>
        {["导师", "学校和学院", "研究方向", "匹配度", "联系"].map((h) => (
          <span key={h} role="columnheader">
            {h}
          </span>
        ))}
        <span role="columnheader" className={TAGS}>
          标签
        </span>
      </div>
      {mentors.map((m) => (
        // The name links to the mentor's page and stretches over the row, so the whole row opens it.
        <div
          key={m.id}
          role="row"
          data-selected={selected.has(m.id) || undefined}
          className={`${COLUMNS} relative h-[62px] rounded-md px-3 outline-ring hover:bg-muted has-focus-visible:outline-2 data-selected:bg-selected ${CARD}`}
        >
          {/* Above the stretched link, with a hit area wider than the box. */}
          <div role="cell" className="relative z-10 flex cards:self-start cards:pt-2.5 cards:[grid-area:check]">
            <Checkbox
              aria-label={`选择${m.name}`}
              disabled={!selectable}
              checked={selected.has(m.id)}
              onCheckedChange={(on) => tick([m.id], on)}
              className="relative after:absolute after:-inset-3"
            />
          </div>
          <div role="cell" className="flex min-w-0 items-center gap-3 cards:[grid-area:who]">
            <Avatar id={m.id} name={m.name} />
            <span className="truncate">
              <Link
                to="/mentors/$mentorId"
                params={{ mentorId: m.id }}
                className="font-medium outline-none after:absolute after:inset-0"
              >
                {m.name}
              </Link>
              {m.affiliation?.title && (
                <span className="ml-1.5 text-sm text-muted-foreground">{m.affiliation.title}</span>
              )}
            </span>
          </div>
          <div role="cell" className="min-w-0 text-sm cards:flex cards:gap-1.5 cards:[grid-area:place]">
            <div className="truncate cards:max-w-full cards:shrink-0">
              {m.affiliation?.university ?? <Missing>未填写学校</Missing>}
              {m.affiliationCount > 1 && (
                <span className="ml-1.5 text-muted-foreground">等 {m.affiliationCount} 个单位</span>
              )}
            </div>
            <div className="truncate text-muted-foreground">
              {[m.affiliation?.school, m.affiliation?.department].filter(Boolean).join(" ") || (
                <Missing>未填写学院</Missing>
              )}
            </div>
          </div>
          <div
            role="cell"
            className="line-clamp-2 text-sm text-muted-foreground cards:line-clamp-1 cards:[grid-area:research]"
          >
            {m.research ?? <Missing>未填写研究方向</Missing>}
          </div>
          <div role="cell" className="cards:justify-self-end cards:[grid-area:match]">
            <Link to="/mentors/$mentorId" params={{ mentorId: m.id }} className="relative z-10 block">
              <Match state={m.matchState} />
            </Link>
          </div>
          <div role="cell" className="min-w-0 text-sm cards:max-w-[45cqw] cards:text-right cards:[grid-area:contact]">
            <ContactLabel contact={m.contact} className="cards:justify-end" />
            {contactDetail(m.contact) && (
              <div className="truncate text-muted-foreground">{contactDetail(m.contact)}</div>
            )}
          </div>
          <div role="cell" className={`flex flex-wrap gap-1 ${TAGS}`}>
            {m.tags.map((t) => (
              <TagChip key={t.id} tag={t} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function Match({ state: { result, state } }: { state: MatchSummary }) {
  return (
    <div className="flex flex-col gap-0.5 text-sm">
      {result && (
        <div className="flex items-center gap-1.5">
          <span className="font-medium tabular-nums">{result.score}</span>
          {result.basisChanged && (
            <span className="size-1.5 rounded-full bg-faint" title={BASIS_CHANGED} aria-label={BASIS_CHANGED} />
          )}
          {!state && (
            <span className="h-1 w-12 overflow-hidden rounded-full bg-muted cards:hidden">
              <span className="block h-full rounded-full bg-muted-foreground" style={{ width: `${result.score}%` }} />
            </span>
          )}
        </div>
      )}
      {state ? (
        <span
          title={state.state === "failed" ? state.error : undefined}
          className={state.state === "failed" ? "text-warning" : "text-muted-foreground"}
        >
          {state.state === "queued" ? "排队中" : state.state === "running" ? "分析中" : "失败"}
        </span>
      ) : (
        !result && <Missing>未分析</Missing>
      )}
    </div>
  );
}

const pageCount = (total: number) => Math.max(1, Math.ceil(total / PAGE_SIZE));

/**
 * Once the last rows of the last page are gone, moves to the page that is now last. `total` is
 * undefined unless the page has just loaded successfully, so a total from before never decides.
 */
export function useLastPage(total: number | undefined, page: number, onPage: (page: number) => void) {
  const go = useEffectEvent(onPage);
  useEffect(() => {
    if (total !== undefined && page > pageCount(total)) go(pageCount(total));
  }, [total, page]);
}

/** "第 2 页，共 9 页" with previous and next. */
export function Pager({ page, total, onPage }: { page: number; total: number; onPage: (page: number) => void }) {
  const pages = pageCount(total);
  return (
    <div className="flex items-center gap-2">
      <span>
        第 {page} 页，共 {pages} 页
      </span>
      <Button
        variant="ghost"
        size="icon"
        className="phone:size-10"
        aria-label="上一页"
        disabled={page <= 1}
        onClick={() => onPage(page - 1)}
      >
        <ChevronLeft strokeWidth={1.6} />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className="phone:size-10"
        aria-label="下一页"
        disabled={page >= pages}
        onClick={() => onPage(page + 1)}
      >
        <ChevronRight strokeWidth={1.6} />
      </Button>
    </div>
  );
}
