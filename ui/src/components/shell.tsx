import { crawlName, refreshCrawls, findSearch } from "@/pages/find";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Outlet } from "@tanstack/react-router";
import { listen } from "@tauri-apps/api/event";
import { MessagesSquare, Settings, Users, ListTodo, LoaderCircle, Search } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { cn } from "cn";
import appIcon from "../../../docs/icon.svg";
import { call, errorMessage } from "@/api/call";
import { startupFailure } from "@/api/app";
import type { MatchFinished, DraftsFinished, CrawlFinished } from "@/api/types";
import { refreshTasks } from "@/pages/tasks";
import { scheduledAt } from "@/components/schedule";
import { ModelAction } from "@/components/ai-writing";
import { refreshMentors, refreshReplyOutcomes } from "@/components/mentor";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { StartupFailure } from "@/components/startup-failure";

const NAV = [
  { to: "/mentors", label: "导师", icon: Users },
  { to: "/conversations", label: "往来", icon: MessagesSquare },
  { to: "/tasks", label: "任务", icon: ListTodo },
  { to: "/find", label: "找导师", icon: Search },
] as const;

// docs/ui.md#布局: settings open from the account row at the bottom of the sidebar; until there are
// accounts, the row is settings itself.
const MORE = { to: "/more", label: "更多", icon: Settings } as const;
const SETTINGS = { to: "/settings", label: "设置", icon: Settings } as const;

/**
 * Sidebar on the window background, content on a rounded page; on a phone-width window the
 * sidebar becomes a tab bar under the page (docs/ui.md#布局).
 */
export function Shell() {
  const failure = useQuery({ queryKey: ["startupFailure"], queryFn: startupFailure, staleTime: Infinity });
  const queryClient = useQueryClient();
  const [completed, setCompleted] = useState<string | null>(null);
  useEffect(() => {
    const crawlChanged = listen("crawl-changed", () => refreshCrawls(queryClient));
    const crawlFinished = listen<CrawlFinished>("crawl-finished", ({ payload: p }) => {
      refreshCrawls(queryClient);
      setCompleted(p.message);
    });
    const stop = listen("matching-changed", () => refreshMentors(queryClient));
    const done = listen<MatchFinished>("matching-finished", ({ payload: p }) => {
      refreshMentors(queryClient);
      setCompleted(`匹配度分析完成：分析了 ${p.analyzed} 位${p.failed > 0 ? `，失败 ${p.failed} 位` : ""}`);
    });
    const tasksChanged = listen("tasks-changed", () => refreshTasks(queryClient));
    const repliesChanged = listen("reply-processing-changed", () => refreshReplyOutcomes(queryClient));
    const draftsDone = listen<DraftsFinished>("drafts-finished", ({ payload: p }) => {
      refreshTasks(queryClient);
      setCompleted(`任务“${p.name}”的草稿写好了：${p.generated} 封，失败 ${p.failed} 封`);
    });
    return () => {
      void crawlChanged.then((f) => f());
      void crawlFinished.then((f) => f());
      void tasksChanged.then((f) => f());
      void repliesChanged.then((f) => f());
      void draftsDone.then((f) => f());
      void stop.then((f) => f());
      void done.then((f) => f());
    };
  }, [queryClient]);
  useEffect(() => {
    if (!completed) return undefined;
    const timer = setTimeout(() => setCompleted(null), 6000);
    return () => clearTimeout(timer);
  }, [completed]);
  // New mail may change any mentor's correspondence and contact status, and a mailbox may have
  // failed to receive or recovered.
  useEffect(() => {
    const stop = listen("received", () => {
      refreshMentors(queryClient);
      for (const key of ["listMailboxes", "receiveStatus"]) {
        void queryClient.invalidateQueries({ queryKey: [key] });
      }
    });
    return () => void stop.then((unlisten) => unlisten());
  }, [queryClient]);
  if (failure.isPending) return null;
  if (failure.data) return <StartupFailure reason={failure.data} />;
  return (
    <div className="flex h-dvh phone:flex-col">
      <aside className="flex w-[228px] shrink-0 flex-col px-3 py-4 phone:hidden">
        <div className="flex items-center gap-2.5 px-2 pb-5 font-semibold">
          <img src={appIcon} alt="" className="size-[30px]" />
          Auto Email Sender
        </div>
        <nav className="flex flex-col gap-0.5">
          {NAV.map((item) => (
            <NavLink key={item.to} {...item} />
          ))}
        </nav>
        <div className="grow" />
        <Background />
        <NavLink {...SETTINGS} />
      </aside>
      <main className="relative my-3 mr-3 flex min-w-0 flex-1 flex-col overflow-hidden rounded-xl bg-background phone:m-0 phone:min-h-0 phone:rounded-none">
        <Outlet />
        {completed && (
          <div
            role="status"
            className="absolute bottom-5 left-5 z-40 flex max-w-[calc(100%-40px)] items-center gap-3 rounded-lg border border-border bg-background px-4 py-3 text-sm shadow-pop phone:bottom-3 phone:left-3"
          >
            <span>{completed}</span>
            <button aria-label="关闭提醒" onClick={() => setCompleted(null)}>
              ×
            </button>
          </div>
        )}
      </main>
      <nav className="hidden auto-cols-fr grid-flow-col border-t border-border bg-background px-1 py-1.5 phone:grid">
        {[...NAV.filter((n) => n.to !== "/find"), MORE].map(({ to, label, icon: Icon }) => (
          <Link
            key={to}
            to={to}
            className="flex flex-col items-center gap-0.5 py-1 text-[11px]"
            activeProps={{ className: "text-brand-text" }}
            inactiveProps={{ className: "text-muted-foreground" }}
          >
            <Icon className="size-[22px]" strokeWidth={1.6} />
            <span className="flex items-center gap-1">
              {label}
              {to === "/conversations" && <UnreadBadge />}
              {to === "/more" && <WaitingDot />}
            </span>
          </Link>
        ))}
      </nav>
    </div>
  );
}

function NavLink({ to, label, icon: Icon }: (typeof NAV)[number] | typeof SETTINGS) {
  return (
    <Link
      to={to}
      className="flex h-10 items-center gap-3 rounded-md px-3"
      activeProps={{ className: "bg-background font-medium text-brand-text" }}
      inactiveProps={{ className: "text-muted-foreground hover:bg-press" }}
    >
      <Icon className="size-[18px]" strokeWidth={1.6} />
      {label}
      {to === "/conversations" && <UnreadBadge className="ml-auto" />}
    </Link>
  );
}

/** How many mentors have new replies; a count that could not be read says so instead. */
function UnreadBadge({ className }: { className?: string }) {
  const unread = useQuery({ queryKey: ["unreadCount"], queryFn: () => call("unreadCount", {}) });
  if (!unread.isError && !unread.data) return null;
  return (
    <span
      title={unread.isError ? `新回信数量读取失败：${errorMessage(unread.error)}` : undefined}
      className={cn("min-w-5 rounded-full bg-unread px-1.5 text-center text-xs leading-5 text-white", className)}
    >
      {unread.isError ? "!" : unread.data > 99 ? "99+" : unread.data}
    </span>
  );
}

/** What runs in the background and what of it waits for the user (docs/ui.md#后台). */
function useBackground() {
  const tasks = useQuery({
    queryKey: ["taskWork"],
    queryFn: () => call("taskWork", {}),
    refetchInterval: 3000,
  });
  const matching = useQuery({
    queryKey: ["matchingProgress"],
    queryFn: () => call("matchingProgress", {}),
    refetchInterval: 3000,
  });
  const crawls = useQuery({
    queryKey: ["crawlWork"],
    queryFn: () => {
      const now = new Date();
      const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
      const end = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1).getTime();
      return call("crawlWork", { start, end });
    },
    refetchInterval: 3000,
  });
  const scheduled = useQuery({
    queryKey: ["scheduledWork"],
    queryFn: () => call("scheduledWork", {}),
    refetchInterval: 3000,
  });
  const replies = useQuery({
    queryKey: ["replyProcessing"],
    queryFn: () => call("replyProcessing", {}),
    refetchInterval: 3000,
  });
  const today = new Date(crawls.dataUpdatedAt).toDateString();
  const work = {
    // Failed and unsendable letters, whichever task they are in, wait together in the sending plan.
    emails: scheduled.data?.pending ?? 0,
    paused: tasks.data?.filter((t) => t.state === "paused") ?? [],
    crawlsPending: crawls.data?.filter((c) => c.state === "paused" || c.state === "failed" || c.failed > 0) ?? [],
    crawling: crawls.data?.filter((c) => c.state === "running") ?? [],
    generating: tasks.data?.filter((t) => t.progress.generating > 0) ?? [],
    matching: matching.data?.remaining ?? 0,
    repliesPaused: replies.data?.pause ?? null,
    marking: replies.data?.pause ? 0 : (replies.data?.remaining ?? 0),
    crawled:
      crawls.data?.filter((c) => c.state === "done" && new Date(c.finishedAt ?? 0).toDateString() === today) ?? [],
    next: scheduled.data?.nextAt ?? null,
    nextToday: scheduled.data?.today ?? 0,
    error: tasks.error ?? matching.error ?? scheduled.error ?? crawls.error ?? replies.error,
  };
  return {
    ...work,
    running: work.crawling.length + work.generating.length + (work.matching > 0 ? 1 : 0) + (work.marking > 0 ? 1 : 0),
    waiting: (work.emails > 0 ? 1 : 0) + work.paused.length + work.crawlsPending.length + (work.repliesPaused ? 1 : 0),
  };
}

/** On a phone the background panel is under "更多", and the tab lights up when something waits. */
function WaitingDot() {
  const { waiting } = useBackground();
  return (
    waiting > 0 && (
      <span role="status" aria-label={`${waiting} 项待处理`} className="size-1.5 rounded-full bg-warning" />
    )
  );
}

function Background({ className }: { className?: string }) {
  const queryClient = useQueryClient();
  const work = useBackground();
  const pauseCrawl = useMutation({
    mutationFn: (id: number) => call("pauseCrawl", { id }),
    onSuccess: () => refreshCrawls(queryClient),
  });
  const resumeReplies = useMutation({
    mutationFn: () => call("resumeReplyProcessing", {}),
    onSuccess: () => refreshReplyOutcomes(queryClient),
  });
  const error = work.error ?? pauseCrawl.error ?? resumeReplies.error;
  return (
    <Popover>
      <PopoverTrigger
        render={
          <button
            className={cn(
              "mb-0.5 flex h-10 items-center gap-3 rounded-md px-3 text-muted-foreground hover:bg-press",
              className,
            )}
          />
        }
      >
        <LoaderCircle className={cn("size-[18px]", work.running > 0 && "animate-spin")} strokeWidth={1.6} />
        后台
        {work.waiting > 0 && (
          <span
            title={`${work.waiting} 项待处理`}
            className="ml-auto min-w-5 rounded-full bg-warning-soft px-1.5 text-center text-xs leading-5 text-warning"
          >
            {work.waiting}
          </span>
        )}
      </PopoverTrigger>
      <PopoverContent side="top" align="start" className="max-h-96 w-80 space-y-4 overflow-y-auto p-4 text-sm">
        {work.waiting > 0 && (
          <Group title="待处理" className="text-warning">
            {work.emails > 0 && (
              <Link to="/tasks" search={{ tab: "plan" }} className={ITEM}>
                <Entry title={`${work.emails} 封邮件待处理`} detail="去发送计划里处理" />
              </Link>
            )}
            {work.paused.map((t) => (
              <Link key={t.id} to="/tasks/$id" params={{ id: t.id }} className={ITEM}>
                <Entry title={t.name} detail={t.pauseReason ?? "已暂停"} />
              </Link>
            ))}
            {work.crawlsPending.map((c) => (
              <Link key={c.id} to="/find" search={findSearch({ tab: "crawl", detail: c.id })} className={ITEM}>
                <Entry
                  title={crawlName(c)}
                  detail={c.error ?? (c.state === "paused" ? "已暂停" : `${c.failed} 个页面没能打开`)}
                />
              </Link>
            ))}
            {work.repliesPaused && (
              <div className="flex items-center gap-2">
                <Link to="/conversations" className={ITEM}>
                  <Entry title="回信处理已暂停" detail={work.repliesPaused.reason} />
                </Link>
                <ModelAction disabled={resumeReplies.isPending} onWrite={() => resumeReplies.mutate()}>
                  继续
                </ModelAction>
              </div>
            )}
          </Group>
        )}
        {work.running > 0 && (
          <Group title="进行中">
            {work.crawling.map((c) => (
              <div key={c.id} className="flex items-center gap-2">
                <Link to="/find" search={findSearch({ tab: "crawl", detail: c.id })} className={ITEM}>
                  {c.stage === "enrich" ? (
                    <Entry title="信息补全" detail={`还剩 ${c.enrichRemaining} 位`} />
                  ) : (
                    <Entry
                      title={crawlName(c)}
                      detail={`已读 ${c.pages} 页，找到 ${c.candidates} 位候选${c.enrichRemaining > 0 ? `，补全还剩 ${c.enrichRemaining} 位` : ""}`}
                    />
                  )}
                </Link>
                <Button
                  variant="quiet"
                  size="sm"
                  disabled={pauseCrawl.isPending}
                  onClick={() => pauseCrawl.mutate(c.id)}
                >
                  暂停
                </Button>
              </div>
            ))}
            {work.generating.map((t) => (
              <Link key={t.id} to="/tasks/$id" params={{ id: t.id }} className={ITEM}>
                <Entry title={t.name} detail={`生成草稿，还剩 ${t.progress.generating} 封`} />
              </Link>
            ))}
            {work.matching > 0 && (
              <Link to="/mentors" className={ITEM}>
                <Entry title="匹配度分析" detail={`还剩 ${work.matching} 位`} />
              </Link>
            )}
            {work.marking > 0 && (
              <Link to="/conversations" className={ITEM}>
                <Entry title="标记回信结果" detail={`还剩 ${work.marking} 封`} />
              </Link>
            )}
          </Group>
        )}
        {work.next !== null && (
          <Group title="按计划进行">
            <Link to="/tasks" search={{ tab: "plan", column: "upcoming" }} className={ITEM}>
              <Entry title={`下一封 ${scheduledAt(work.next)}`} detail={`今天还剩 ${work.nextToday} 封`} />
            </Link>
          </Group>
        )}
        {work.crawled.length > 0 && (
          <Group title="今天完成">
            {work.crawled.map((c) => (
              <Link key={c.id} to="/find" search={findSearch({ crawl: c.id })} className={ITEM}>
                <Entry title={crawlName(c)} detail={`找到 ${c.candidates} 位候选，去审核`} />
              </Link>
            ))}
          </Group>
        )}
        {work.waiting === 0 && work.running === 0 && work.next === null && work.crawled.length === 0 && (
          <p className="text-muted-foreground">现在没有后台工作</p>
        )}
        {error && <p className="text-destructive">{errorMessage(error)}</p>}
      </PopoverContent>
    </Popover>
  );
}

function Group({ title, className, children }: { title: string; className?: string; children: ReactNode }) {
  return (
    <section>
      <h2 className={cn("mb-1 text-xs font-medium text-muted-foreground", className)}>{title}</h2>
      {children}
    </section>
  );
}

const ITEM = "-mx-2 block min-w-0 grow rounded-md px-2 py-1.5 hover:bg-press";

function Entry({ title, detail }: { title: string; detail: string }) {
  return (
    <>
      <span className="block truncate">{title}</span>
      <span className="block text-xs text-muted-foreground">{detail}</span>
    </>
  );
}

export function MorePage() {
  return (
    <div className="p-5">
      <h1 className="mb-5 text-2xl font-semibold">更多</h1>
      <Link to="/find" search={findSearch({})} className="mb-3 flex h-12 items-center gap-3 rounded-md bg-muted px-4">
        <Search className="size-5" />
        找导师
      </Link>
      <Link to="/settings" className="mb-3 flex h-12 items-center gap-3 rounded-md bg-muted px-4">
        <Settings className="size-5" />
        设置
      </Link>
      <Background className="h-12 w-full rounded-md bg-muted px-4 text-foreground [&>svg]:size-5" />
    </div>
  );
}
