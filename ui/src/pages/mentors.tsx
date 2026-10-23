import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";
import { type ReactNode, useEffect, useEffectEvent, useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { Contact, ContactStatus, MatchState, MentorRow } from "@/api/types";

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
  const search = useSearch({ from: "/" });
  const navigate = useNavigate({ from: "/" });
  const page = search.page ?? 1;
  const query = useQuery({
    queryKey: ["listMentors", search],
    queryFn: () => call("listMentors", { status: search.status, keyword: search.q, page }),
    placeholderData: keepPreviousData,
  });
  const total = query.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex items-center gap-4 px-7 pt-6 pb-4">
        <div>
          <h1 className="text-2xl font-semibold">导师</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">{query.data ? `共 ${total.toLocaleString()} 位` : " "}</p>
        </div>
        <div className="grow" />
        <SearchBox
          value={search.q ?? ""}
          onChange={(q) => navigate({ search: (s) => ({ ...s, q: q || undefined, page: undefined }) })}
        />
      </header>

      <div className="px-7 pb-3">
        <div role="tablist" aria-label="联系状态" className="inline-flex gap-0.5 rounded-full bg-muted p-[3px]">
          {STATUSES.map(([status, label]) => (
            <button
              key={label}
              role="tab"
              aria-selected={search.status === status}
              onClick={() => navigate({ search: (s) => ({ ...s, status, page: undefined }) })}
              className="h-[30px] rounded-full px-3.5 text-sm text-muted-foreground aria-selected:bg-background aria-selected:font-medium aria-selected:text-foreground aria-selected:shadow-sm dark:aria-selected:bg-input"
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4">
        {query.isError ? (
          <Notice title="导师列表没能加载" detail={errorMessage(query.error)} />
        ) : query.data && query.data.mentors.length === 0 ? (
          search.status || search.q ? (
            <Notice title="没有符合条件的导师" detail="换个关键词或联系状态再试。" />
          ) : (
            <Notice title="还没有导师" detail="从学院官网智能抓取、从导师库添加，或导入表格。" />
          )
        ) : (
          <MentorTable mentors={query.data?.mentors ?? []} />
        )}
      </div>

      <footer className="flex items-center justify-end gap-2 px-7 py-3 text-sm text-muted-foreground">
        <span>
          第 {page} 页，共 {pages} 页
        </span>
        <PageButton
          label="上一页"
          disabled={page <= 1}
          onClick={() => navigate({ search: (s) => ({ ...s, page: page - 1 > 1 ? page - 1 : undefined }) })}
        >
          <ChevronLeft className="size-[18px]" strokeWidth={1.6} />
        </PageButton>
        <PageButton
          label="下一页"
          disabled={page >= pages}
          onClick={() => navigate({ search: (s) => ({ ...s, page: page + 1 }) })}
        >
          <ChevronRight className="size-[18px]" strokeWidth={1.6} />
        </PageButton>
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
    <label className="flex h-10 w-[320px] items-center gap-2 rounded-full bg-muted px-4 text-muted-foreground focus-within:ring-2 focus-within:ring-ring">
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

// docs/ui.md#布局: no tag column at 1240px and below.
const COLUMNS =
  "grid grid-cols-[minmax(150px,1.1fr)_minmax(170px,1.2fr)_minmax(180px,1.8fr)_110px_minmax(176px,1fr)_minmax(64px,0.5fr)] items-center gap-3 max-[1240px]:grid-cols-[minmax(140px,1.1fr)_minmax(150px,1.2fr)_minmax(140px,1.4fr)_110px_minmax(170px,1fr)]";
const TAGS = "max-[1240px]:hidden";

function MentorTable({ mentors }: { mentors: MentorRow[] }) {
  return (
    <div role="table" aria-label="导师" className="flex flex-col gap-[3px] pb-4">
      <div role="row" className={`${COLUMNS} px-3 pb-1 text-sm text-muted-foreground`}>
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
        <div key={m.id} role="row" className={`${COLUMNS} h-[62px] rounded-md px-3 hover:bg-muted`}>
          <div role="cell" className="flex min-w-0 items-center gap-3">
            <span
              className={`grid size-9 shrink-0 place-items-center rounded-full text-sm font-medium ${AVATARS[m.id % AVATARS.length]}`}
            >
              {m.name.slice(0, 1)}
            </span>
            <span className="truncate">
              <span className="font-medium">{m.name}</span>
              {m.title && <span className="ml-1.5 text-sm text-muted-foreground">{m.title}</span>}
            </span>
          </div>
          <div role="cell" className="min-w-0 text-sm">
            <div className="truncate">{m.university ?? <Missing>未填写学校</Missing>}</div>
            <div className="truncate text-muted-foreground">
              {[m.school, m.department].filter(Boolean).join(" ") || <Missing>未填写学院</Missing>}
            </div>
          </div>
          <div role="cell" className="line-clamp-2 text-sm text-muted-foreground">
            {m.research ?? <Missing>未填写研究方向</Missing>}
          </div>
          <div role="cell">
            <Match state={m.matchState} />
          </div>
          <div role="cell" className="min-w-0 text-sm">
            <ContactCell contact={m.contact} />
          </div>
          <div role="cell" className={`flex flex-wrap gap-1 ${TAGS}`}>
            {m.tags.map((t) => (
              <span
                key={t.name}
                className="rounded-full px-2 py-0.5 text-xs"
                style={{ color: t.color, backgroundColor: `color-mix(in srgb, ${t.color} 12%, transparent)` }}
              >
                {t.name}
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

const AVATARS = ["bg-avatar-1", "bg-avatar-2", "bg-avatar-3", "bg-avatar-4", "bg-avatar-5"];

function Missing({ children }: { children: string }) {
  return <span className="text-faint">{children}</span>;
}

function Match({ state }: { state: MatchState }) {
  if (state.state === "done") {
    return (
      <div className="flex items-center gap-2">
        <span className="w-6 font-medium">{state.score}</span>
        <span className="h-1 w-14 overflow-hidden rounded-full bg-muted">
          <span className="block h-full rounded-full bg-muted-foreground" style={{ width: `${state.score}%` }} />
        </span>
      </div>
    );
  }
  const [label, color] = MATCH_LABEL[state.state];
  return <span className={`text-sm ${color}`}>{label}</span>;
}

const MATCH_LABEL: Record<Exclude<MatchState["state"], "done">, [string, string]> = {
  running: ["分析中", "text-muted-foreground"],
  queued: ["排队中", "text-muted-foreground"],
  failed: ["分析失败", "text-warning"],
  not_analyzed: ["未分析", "text-faint"],
};

const STATUS_STYLE: Record<ContactStatus, [string, string]> = {
  none: ["未联系", "border border-input"],
  contacted: ["已联系", "bg-info"],
  replied: ["已回复", "bg-success"],
  bounced: ["退信", "bg-warning"],
};

function ContactCell({ contact }: { contact: Contact }) {
  const [label, dot] = STATUS_STYLE[contact.status];
  const detail =
    contact.status === "contacted"
      ? `${times(contact.count)}${day(contact.lastOutAt)}发出`
      : contact.status === "replied"
        ? `${times(contact.count)}${day(contact.lastReplyAt)}回复`
        : contact.status === "bounced"
          ? `${day(contact.lastOutAt)}退信`
          : null;
  return (
    <>
      <div className="flex items-center gap-1.5">
        <span className={`size-2 rounded-full ${dot}`} />
        {label}
        {contact.status === "replied" && contact.unread && (
          <span className="size-2 rounded-full bg-unread" aria-label="有新回信" />
        )}
      </div>
      {detail && <div className="truncate text-muted-foreground">{detail}</div>}
    </>
  );
}

/** Nothing for zero: a mentor who wrote first, or was only answered elsewhere, has no counted email. */
function times(count: number): string {
  return count > 0 ? `联系 ${count} 次，` : "";
}

/** "今天", "昨天", or the date, in the user's time zone. */
function day(ms: number): string {
  const date = new Date(ms);
  const today = new Date();
  const days = Math.round((startOfDay(today) - startOfDay(date)) / 86_400_000);
  if (days === 0) return "今天";
  if (days === 1) return "昨天";
  const md = `${date.getMonth() + 1}月${date.getDate()}日`;
  return date.getFullYear() === today.getFullYear() ? md : `${date.getFullYear()}年${md}`;
}

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function Notice({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="grid h-full place-items-center text-center">
      <div>
        <p className="text-lg font-medium">{title}</p>
        <p className="mt-1 text-sm text-muted-foreground">{detail}</p>
      </div>
    </div>
  );
}

function PageButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="grid size-9 place-items-center rounded-md hover:bg-muted disabled:opacity-40 disabled:hover:bg-transparent"
    >
      {children}
    </button>
  );
}
