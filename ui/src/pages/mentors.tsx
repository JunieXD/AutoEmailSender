import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { ChevronLeft, ChevronRight, Plus, Search } from "lucide-react";
import { useEffect, useEffectEvent, useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { ContactStatus, MatchState, MentorRow } from "@/api/types";
import { EMPTY_FORM, MentorDialog } from "@/components/mentor-dialog";
import { Avatar, ContactLabel, contactDetail, MATCH_LABEL, Missing } from "@/components/mentor";
import { TagChip } from "@/components/tags";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";

const PAGE_SIZE = 50;

const STATUSES: [ContactStatus | undefined, string][] = [
  [undefined, "全部"],
  ["none", "未联系"],
  ["contacted", "已联系"],
  ["replied", "已回复"],
  ["bounced", "退信"],
];

export type MentorSearch = { status?: ContactStatus; q?: string; page?: number };

/** The list's filters live in the URL so they survive going back; anything unexpected is dropped. */
export function mentorSearch(search: Record<string, unknown>): MentorSearch {
  const status = STATUSES.find(([s]) => s !== undefined && s === search.status)?.[0];
  const q = typeof search.q === "string" && search.q !== "" ? search.q : undefined;
  const page =
    typeof search.page === "number" && Number.isInteger(search.page) && search.page > 1 ? search.page : undefined;
  return { status, q, page };
}

export function MentorsPage() {
  const search = useSearch({ from: "/mentors" });
  const navigate = useNavigate({ from: "/mentors" });
  const page = search.page ?? 1;
  const query = useQuery({
    queryKey: ["listMentors", search],
    queryFn: () => call("listMentors", { status: search.status, keyword: search.q, page }),
    placeholderData: keepPreviousData,
  });
  const total = query.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex items-center gap-4 px-7 pt-6 pb-4 phone:flex-wrap phone:gap-y-2.5 phone:px-4 phone:pt-3.5 phone:pb-2.5">
        <div>
          <h1 className="text-2xl font-semibold phone:text-[22px]">导师</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">{query.data ? `共 ${total.toLocaleString()} 位` : " "}</p>
        </div>
        <div className="grow" />
        <SearchBox
          value={search.q ?? ""}
          onChange={(q) => navigate({ search: (s) => ({ ...s, q: q || undefined, page: undefined }) })}
        />
        <Button aria-label="添加导师" className="phone:px-2.5" onClick={() => setAdding(true)}>
          <Plus strokeWidth={1.6} />
          <span className="phone:hidden">添加导师</span>
        </Button>
        {adding && (
          <MentorDialog
            title="添加导师"
            action="添加"
            initial={EMPTY_FORM}
            save={(form) => call("addMentor", form)}
            onSaved={({ id }) => {
              void queryClient.invalidateQueries({ queryKey: ["listMentors"] });
              void navigate({ to: "/mentors/$mentorId", params: { mentorId: id } });
            }}
            onClose={() => setAdding(false)}
          />
        )}
      </header>

      <div className="px-7 pb-3 phone:overflow-x-auto phone:px-4 phone:pb-2.5 phone:[scrollbar-width:none]">
        <div role="tablist" aria-label="联系状态" className="inline-flex gap-0.5 rounded-full bg-muted p-[3px]">
          {STATUSES.map(([status, label]) => (
            <button
              key={label}
              role="tab"
              aria-selected={search.status === status}
              onClick={() => navigate({ search: (s) => ({ ...s, status, page: undefined }) })}
              className="h-[30px] rounded-full px-3.5 text-sm whitespace-nowrap phone:h-10 text-muted-foreground aria-selected:bg-background aria-selected:font-medium aria-selected:text-foreground aria-selected:shadow-sm dark:aria-selected:bg-input"
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="@container min-h-0 flex-1 overflow-y-auto px-4 phone:px-0">
        {query.isError ? (
          <Notice title="导师列表没能加载" detail={errorMessage(query.error)} />
        ) : query.data && query.data.mentors.length === 0 ? (
          search.status || search.q ? (
            <Notice title="没有符合条件的导师" detail="换个关键词或联系状态再试。" />
          ) : (
            <Notice title="还没有导师" detail="点“添加导师”填写导师的资料。" />
          )
        ) : (
          <MentorTable mentors={query.data?.mentors ?? []} />
        )}
      </div>

      <footer className="flex items-center justify-end gap-2 px-7 py-3 text-sm text-muted-foreground phone:justify-center phone:px-4">
        <span>
          第 {page} 页，共 {pages} 页
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="phone:size-10"
          aria-label="上一页"
          disabled={page <= 1}
          onClick={() => navigate({ search: (s) => ({ ...s, page: page - 1 > 1 ? page - 1 : undefined }) })}
        >
          <ChevronLeft strokeWidth={1.6} />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="phone:size-10"
          aria-label="下一页"
          disabled={page >= pages}
          onClick={() => navigate({ search: (s) => ({ ...s, page: page + 1 }) })}
        >
          <ChevronRight strokeWidth={1.6} />
        </Button>
      </footer>
    </div>
  );
}

function SearchBox({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [text, setText] = useState(value);
  // Follow the URL when it changes from elsewhere, such as going back.
  const [shown, setShown] = useState(value);
  if (value !== shown) {
    setShown(value);
    setText(value);
  }
  const commit = useEffectEvent(onChange);
  useEffect(() => {
    const timer = setTimeout(() => {
      if (text.trim() !== value) commit(text.trim());
    }, 300);
    return () => clearTimeout(timer);
  }, [text, value]);
  return (
    <label className="flex h-10 w-[320px] items-center gap-2 rounded-full bg-muted px-4 text-muted-foreground focus-within:ring-2 focus-within:ring-ring phone:order-3 phone:w-full">
      <Search className="size-[18px] shrink-0" strokeWidth={1.6} />
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="搜索姓名、学校、研究方向"
        aria-label="搜索导师"
        className="min-w-0 flex-1 bg-transparent text-foreground outline-none placeholder:text-faint"
      />
    </label>
  );
}

// docs/ui.md#布局: no tag column on narrow windows; each row is a card where the columns do not fit.
const COLUMNS =
  "grid grid-cols-[minmax(150px,1.1fr)_minmax(170px,1.2fr)_minmax(180px,1.8fr)_110px_minmax(176px,1fr)_minmax(64px,0.5fr)] items-center gap-3 narrow:grid-cols-[minmax(140px,1.1fr)_minmax(150px,1.2fr)_minmax(140px,1.4fr)_110px_minmax(170px,1fr)]";
const CARD =
  "cards:h-auto cards:grid-cols-[minmax(0,1fr)_auto] cards:gap-x-3 cards:gap-y-1 cards:rounded-none cards:border-b cards:border-border cards:px-4 cards:py-3 cards:[grid-template-areas:'who_match'_'place_contact'_'research_research']";
const TAGS = "narrow:hidden";

function MentorTable({ mentors }: { mentors: MentorRow[] }) {
  return (
    <div
      role="table"
      aria-label="导师"
      className="flex flex-col gap-[3px] pb-4 cards:gap-0 cards:border-t cards:border-border cards:pb-0"
    >
      <div role="row" className={`${COLUMNS} px-3 pb-1 text-sm text-muted-foreground cards:hidden`}>
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
          className={`${COLUMNS} relative h-[62px] rounded-md px-3 outline-ring hover:bg-muted has-focus-visible:outline-2 ${CARD}`}
        >
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
              {m.title && <span className="ml-1.5 text-sm text-muted-foreground">{m.title}</span>}
            </span>
          </div>
          <div role="cell" className="min-w-0 text-sm cards:flex cards:gap-1.5 cards:[grid-area:place]">
            <div className="truncate cards:max-w-full cards:shrink-0">
              {m.university ?? <Missing>未填写学校</Missing>}
            </div>
            <div className="truncate text-muted-foreground">
              {[m.school, m.department].filter(Boolean).join(" ") || <Missing>未填写学院</Missing>}
            </div>
          </div>
          <div
            role="cell"
            className="line-clamp-2 text-sm text-muted-foreground cards:line-clamp-1 cards:[grid-area:research]"
          >
            {m.research ?? <Missing>未填写研究方向</Missing>}
          </div>
          <div role="cell" className="cards:justify-self-end cards:[grid-area:match]">
            <Match state={m.matchState} />
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

function Match({ state }: { state: MatchState }) {
  if (state.state === "done") {
    return (
      <div className="flex items-center gap-2">
        <span className="w-6 font-medium">{state.score}</span>
        <span className="h-1 w-14 overflow-hidden rounded-full bg-muted cards:w-10">
          <span className="block h-full rounded-full bg-muted-foreground" style={{ width: `${state.score}%` }} />
        </span>
      </div>
    );
  }
  const [label, color] = MATCH_LABEL[state.state];
  return <span className={`text-sm ${color}`}>{label}</span>;
}
