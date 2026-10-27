import { crawlName, refreshCrawls, findSearch } from "@/pages/find";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Outlet, useLocation, useNavigate } from "@tanstack/react-router";
import { listen } from "@tauri-apps/api/event";
import { MessagesSquare, Settings, Users, ListTodo, LoaderCircle, Search, ChartNoAxesColumn } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { cn } from "cn";
import appIcon from "../../../docs/icon.svg";
import { call, errorMessage } from "@/api/call";
import { startupFailure } from "@/api/app";
import type { MatchFinished, DraftsFinished, CrawlFinished } from "@/api/types";
import { refreshTasks } from "@/components/task-cache";
import { scheduledAt } from "@/components/schedule";
import { ModelAction } from "@/components/ai-writing";
import { refreshMentors, refreshReplyOutcomes } from "@/components/mentor";
import { refreshTodos } from "@/components/todos";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { StartupFailure } from "@/components/startup-failure";
import { AgentRequests } from "@/components/agent-requests";
import { LibraryUpdatesDialog } from "@/components/library";
import { useAccount } from "@/components/account";
import { UpdateEntry } from "@/components/update";
import { useResume } from "@/pages/mailboxes";
import { useReceive } from "@/pages/conversations";

const NAV = [
  { to: "/mentors", label: "导师", icon: Users },
  { to: "/conversations", label: "往来", icon: MessagesSquare },
  { to: "/tasks", label: "任务", icon: ListTodo },
  { to: "/find", label: "找导师", icon: Search },
  { to: "/stats", label: "统计", icon: ChartNoAxesColumn },
] as const;

// docs/ui.md#布局: settings open from the account row at the bottom of the sidebar.
const MORE = { to: "/more", label: "更多", icon: Settings } as const;
const SETTINGS = { to: "/settings", label: "设置", icon: Settings } as const;

/**
 * Sidebar on the window background, content on a rounded page; on a phone-width window the
 * sidebar becomes a tab bar under the page (docs/ui.md#布局).
 */
const focusSearch = () => document.getElementById("mentor-search")?.focus();
// macOS keeps Ctrl+K for deleting to the end of the line; Windows takes Win+K for itself.
const SEARCH_KEY = navigator.userAgent.includes("Mac") ? "metaKey" : "ctrlKey";

export function Shell() {
  const failure = useQuery({ queryKey: ["startupFailure"], queryFn: startupFailure, staleTime: Infinity });
  const queryClient = useQueryClient();
  const [completed, setCompleted] = useState<string | null>(null);
  const navigate = useNavigate();
  const pathname = useLocation({ select: (l) => l.pathname });
  // docs/ui.md#快捷键: ⌘K (Ctrl+K on Windows) searches mentors from anywhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e[SEARCH_KEY] || e.key.toLowerCase() !== "k" || e.defaultPrevented) return;
      // Leaving the page would close the dialog and lose what was typed in it.
      if (document.querySelector('[role="dialog"]')) return;
      e.preventDefault();
      if (pathname === "/mentors") focusSearch();
      else void navigate({ to: "/mentors" }).then(focusSearch);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate, pathname]);
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
    // A sent email changes its mentor's correspondence and contact status as well as its task.
    const sendingChanged = listen("sending-changed", () => refreshMentors(queryClient));
    const tasksChanged = listen("tasks-changed", () => refreshTasks(queryClient));
    const repliesChanged = listen("reply-processing-changed", () => refreshReplyOutcomes(queryClient));
    const todosChanged = listen("todos-changed", () => refreshTodos(queryClient));
    const updateChanged = listen("update-changed", () => queryClient.invalidateQueries({ queryKey: ["updateStatus"] }));
    const draftsDone = listen<DraftsFinished>("drafts-finished", ({ payload: p }) => {
      refreshTasks(queryClient);
      setCompleted(`任务“${p.name}”的草稿写好了：${p.generated} 封，失败 ${p.failed} 封`);
    });
    return () => {
      void crawlChanged.then((f) => f());
      void crawlFinished.then((f) => f());
      void sendingChanged.then((f) => f());
      void tasksChanged.then((f) => f());
      void repliesChanged.then((f) => f());
      void todosChanged.then((f) => f());
      void updateChanged.then((f) => f());
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
  // What an Agent did may touch anything on screen. It often calls many operations in a row, so
  // refresh at most twice a second while it works.
  useEffect(() => {
    let waiting = false;
    const stop = listen("agent-called", () => {
      if (waiting) return;
      waiting = true;
      setTimeout(() => {
        waiting = false;
        void queryClient.invalidateQueries();
      }, 500);
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
        <UpdateEntry />
        <Background />
        <AccountRow />
      </aside>
      <main className="relative my-3 mr-3 flex min-w-0 flex-1 flex-col overflow-hidden rounded-xl bg-background phone:m-0 phone:min-h-0 phone:rounded-none">
        <Outlet />
        <AgentRequests />
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
        {[...NAV.filter((n) => n.to !== "/find" && n.to !== "/stats"), MORE].map(({ to, label, icon: Icon }) => (
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

function NavLink({ to, label, icon: Icon }: Omit<(typeof NAV)[number] | typeof SETTINGS, "label"> & { label: string }) {
  return (
    <Link
      to={to}
      className="flex h-10 items-center gap-3 rounded-md px-3"
      activeProps={{ className: "bg-background font-medium text-brand-text" }}
      inactiveProps={{ className: "text-muted-foreground hover:bg-press" }}
    >
      <Icon className="size-[18px] shrink-0" strokeWidth={1.6} />
      <span className="truncate">{label}</span>
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
/** Today in the user's time zone, which the core cannot know. */
function localToday() {
  const now = new Date();
  return {
    start: new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime(),
    end: new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1).getTime(),
  };
}

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
    queryFn: () => call("crawlWork", localToday()),
    refetchInterval: 3000,
  });
  const done = useQuery({
    queryKey: ["todayWork"],
    queryFn: () => call("todayWork", localToday()),
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
  const mailboxes = useQuery({
    queryKey: ["listMailboxes"],
    queryFn: () => call("listMailboxes", {}),
    refetchInterval: 3000,
  });
  const library = useQuery({
    queryKey: ["libraryUpdateCount"],
    queryFn: () => call("libraryUpdateCount", {}),
    refetchInterval: 3000,
  });
  const storage = useQuery({
    queryKey: ["storageFailure"],
    queryFn: () => call("storageFailure", {}),
    refetchInterval: 3000,
  });
  const today = new Date(crawls.dataUpdatedAt).toDateString();
  const work = {
    storage: storage.data ?? null,
    // Failed and unsendable letters, whichever task they are in, wait together in the sending plan.
    emails: scheduled.data?.pending ?? 0,
    mailboxes: mailboxes.data?.flatMap(({ id, address, pause }) => (pause ? [{ id, address, pause }] : [])) ?? [],
    // A round that could not reach a mailbox still counts as received; the reason stays on the mailbox.
    unreceived:
      mailboxes.data?.flatMap(({ id, address, receiveError }) =>
        receiveError ? [{ id, address, receiveError }] : [],
      ) ?? [],
    paused: tasks.data?.filter((t) => t.state === "paused") ?? [],
    crawlsPending: crawls.data?.filter((c) => c.state === "paused" || c.state === "failed" || c.failed > 0) ?? [],
    crawling: crawls.data?.filter((c) => c.state === "running") ?? [],
    generating: tasks.data?.filter((t) => t.generating > 0 && t.state !== "paused") ?? [],
    matching: matching.data?.remaining ?? 0,
    matchingPause: matching.data?.pause ?? null,
    receiving: (mailboxes.data?.length ?? 0) > 0,
    repliesPaused: replies.data?.pause ?? null,
    libraryUpdates: library.data ?? 0,
    marking: replies.data?.pause ? 0 : (replies.data?.remaining ?? 0),
    crawled:
      crawls.data?.filter((c) => c.state === "done" && new Date(c.finishedAt ?? 0).toDateString() === today) ?? [],
    drafted: done.data?.drafts ?? [],
    matched: done.data?.matched ?? 0,
    sent: done.data?.sent ?? 0,
    next: scheduled.data?.nextAt ?? null,
    nextToday: scheduled.data?.today ?? 0,
    error:
      storage.error ??
      tasks.error ??
      matching.error ??
      scheduled.error ??
      crawls.error ??
      mailboxes.error ??
      replies.error ??
      library.error ??
      done.error,
  };
  return {
    ...work,
    finished: work.crawled.length + work.drafted.length + (work.matched > 0 ? 1 : 0) + (work.sent > 0 ? 1 : 0),
    running:
      work.crawling.length +
      work.generating.length +
      (work.matching > 0 && !work.matchingPause ? 1 : 0) +
      (work.marking > 0 ? 1 : 0),
    waiting:
      (work.storage ? 1 : 0) +
      (work.emails > 0 ? 1 : 0) +
      work.mailboxes.length +
      work.unreceived.length +
      work.paused.length +
      work.crawlsPending.length +
      (work.matching > 0 && work.matchingPause ? 1 : 0) +
      (work.repliesPaused ? 1 : 0) +
      (work.libraryUpdates > 0 ? 1 : 0),
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
  const crawl = useMutation({
    mutationFn: ({ op, id }: { op: "pauseCrawl" | "resumeCrawl"; id: number }) => call(op, { id }),
    onSuccess: () => refreshCrawls(queryClient),
  });
  const matching = useMutation({
    mutationFn: (op: "pauseMatching" | "resumeMatching") => call(op, {}),
    onSettled: () => refreshMentors(queryClient),
  });
  const pauseTask = useMutation({
    mutationFn: (id: number) => call("pauseTask", { id }),
    onSuccess: () => refreshTasks(queryClient),
  });
  const resumeReplies = useMutation({
    mutationFn: () => call("resumeReplyProcessing", {}),
    onSuccess: () => refreshReplyOutcomes(queryClient),
  });
  const resume = useResume();
  const { receive, receiving, receiveError, received } = useReceive();
  const [open, setOpen] = useState(false);
  const [libraryUpdates, setLibraryUpdates] = useState(false);
  const error =
    work.error ??
    crawl.error ??
    matching.error ??
    pauseTask.error ??
    resume.error ??
    resumeReplies.error ??
    receiveError;
  return (
    <Popover open={open} onOpenChange={setOpen}>
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
            {work.storage && (
              <Link to="/settings/about" className={ITEM}>
                <Entry title="数据暂时无法保存" detail={work.storage} />
              </Link>
            )}
            {work.emails > 0 && (
              <Link to="/tasks" search={{ tab: "plan" }} className={ITEM}>
                <Entry title={`${work.emails} 封邮件待处理`} detail="去发送计划里处理" />
              </Link>
            )}
            {work.mailboxes.map((m) => (
              <div key={m.id} className="flex items-center gap-2">
                <Link to="/settings/mailboxes" className={ITEM}>
                  <Entry title={m.address} detail={`发信暂停到 ${scheduledAt(m.pause.until)}：${m.pause.reason}`} />
                </Link>
                <Button variant="quiet" size="sm" disabled={resume.isPending} onClick={() => resume.mutate(m.id)}>
                  恢复
                </Button>
              </div>
            ))}
            {work.unreceived.map((m) => (
              <Link key={m.id} to="/settings/mailboxes" className={ITEM}>
                <Entry title={m.address} detail={`收信失败：${m.receiveError}`} />
              </Link>
            ))}
            {work.paused.map((t) => (
              <Link key={t.id} to="/tasks/$id" params={{ id: t.id }} className={ITEM}>
                <Entry title={t.name} detail={t.pauseReason ?? "已暂停"} />
              </Link>
            ))}
            {work.crawlsPending.map((c) => (
              <div key={c.id} className="flex items-center gap-2">
                <Link to="/find" search={findSearch({ tab: "crawl", detail: c.id })} className={ITEM}>
                  <Entry
                    title={crawlName(c)}
                    detail={c.error ?? (c.state === "paused" ? "已暂停" : `${c.failed} 个页面没能打开`)}
                  />
                </Link>
                {c.state === "paused" && (
                  <ModelAction
                    disabled={crawl.isPending}
                    modelIds={c.modelIds}
                    onWrite={() => crawl.mutate({ op: "resumeCrawl", id: c.id })}
                  >
                    继续
                  </ModelAction>
                )}
              </div>
            ))}
            {work.matching > 0 && work.matchingPause && (
              <div className="flex items-center gap-2">
                <Link to="/mentors" className={ITEM}>
                  <Entry title="匹配度分析已暂停" detail={work.matchingPause.reason ?? `还剩 ${work.matching} 位`} />
                </Link>
                <ModelAction disabled={matching.isPending} onWrite={() => matching.mutate("resumeMatching")}>
                  继续
                </ModelAction>
              </div>
            )}
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
            {work.libraryUpdates > 0 && (
              <button
                className={cn(ITEM, "w-full text-left")}
                onClick={() => {
                  setOpen(false);
                  setLibraryUpdates(true);
                }}
              >
                <Entry title={`导师库有 ${work.libraryUpdates} 位导师的资料有更新`} detail="打开去选" />
              </button>
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
                  disabled={crawl.isPending}
                  onClick={() => crawl.mutate({ op: "pauseCrawl", id: c.id })}
                >
                  暂停
                </Button>
              </div>
            ))}
            {work.generating.map((t) => (
              <div key={t.id} className="flex items-center gap-2">
                <Link to="/tasks/$id" params={{ id: t.id }} className={ITEM}>
                  <Entry title={t.name} detail={`生成草稿，还剩 ${t.generating} 封`} />
                </Link>
                <Button variant="quiet" size="sm" disabled={pauseTask.isPending} onClick={() => pauseTask.mutate(t.id)}>
                  暂停
                </Button>
              </div>
            ))}
            {work.matching > 0 && !work.matchingPause && (
              <div className="flex items-center gap-2">
                <Link to="/mentors" className={ITEM}>
                  <Entry title="匹配度分析" detail={`还剩 ${work.matching} 位`} />
                </Link>
                <Button
                  variant="quiet"
                  size="sm"
                  disabled={matching.isPending}
                  onClick={() => matching.mutate("pauseMatching")}
                >
                  暂停
                </Button>
              </div>
            )}
            {work.marking > 0 && (
              <Link to="/conversations" className={ITEM}>
                <Entry title="处理回信" detail={`还剩 ${work.marking} 项`} />
              </Link>
            )}
          </Group>
        )}
        {(work.next !== null || work.receiving) && (
          <Group title="按计划进行">
            {work.next !== null && (
              <Link to="/tasks" search={{ tab: "plan", column: "upcoming" }} className={ITEM}>
                <Entry title={`下一封 ${scheduledAt(work.next)}`} detail={`今天还剩 ${work.nextToday} 封`} />
              </Link>
            )}
            {work.receiving && (
              <div className="flex items-center gap-2">
                <Link to="/conversations" className={ITEM}>
                  <Entry title="收信" detail={received ?? "还没有收过信"} />
                </Link>
                <Button variant="quiet" size="sm" disabled={receiving} onClick={receive}>
                  立即收信
                </Button>
              </div>
            )}
          </Group>
        )}
        {work.finished > 0 && (
          <Group title="今天完成">
            {work.crawled.map((c) => (
              <Link key={c.id} to="/find" search={findSearch({ crawl: c.id })} className={ITEM}>
                <Entry title={crawlName(c)} detail={`找到 ${c.candidates} 位候选，去审核`} />
              </Link>
            ))}
            {work.drafted.map((t) => (
              <Link key={t.id} to="/tasks/$id" params={{ id: t.id }} className={ITEM}>
                <Entry title={t.name} detail={`写好 ${t.generated} 封草稿，去审核`} />
              </Link>
            ))}
            {work.matched > 0 && (
              <Link to="/mentors" className={ITEM}>
                <Entry title="匹配度分析" detail={`分析了 ${work.matched} 位，去导师列表`} />
              </Link>
            )}
            {work.sent > 0 && (
              <Link to="/tasks" search={{ tab: "plan", column: "history" }} className={ITEM}>
                <Entry title="发信" detail={`发出 ${work.sent} 封，去发送历史`} />
              </Link>
            )}
          </Group>
        )}
        {work.waiting === 0 && work.running === 0 && work.next === null && !work.receiving && work.finished === 0 && (
          <p className="text-muted-foreground">现在没有后台工作</p>
        )}
        {error && <p className="text-destructive">{errorMessage(error)}</p>}
      </PopoverContent>
      {libraryUpdates && <LibraryUpdatesDialog onClose={() => setLibraryUpdates(false)} />}
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

/** The account row's label: the account's email once there is one. */
function useSettingsLabel() {
  const account = useAccount().data;
  return account && account.kind !== "loggedOut" ? account.email : SETTINGS.label;
}

function AccountRow() {
  const label = useSettingsLabel();
  return <NavLink {...SETTINGS} label={label} />;
}

export function MorePage() {
  const settings = useSettingsLabel();
  return (
    <div className="p-5">
      <h1 className="mb-5 text-2xl font-semibold">更多</h1>
      <Link to="/find" search={findSearch({})} className="mb-3 flex h-12 items-center gap-3 rounded-md bg-muted px-4">
        <Search className="size-5" />
        找导师
      </Link>
      <Link to="/stats" className="mb-3 flex h-12 items-center gap-3 rounded-md bg-muted px-4">
        <ChartNoAxesColumn className="size-5" />
        统计
      </Link>
      <Link to="/settings" className="mb-3 flex h-12 items-center gap-3 rounded-md bg-muted px-4">
        <Settings className="size-5 shrink-0" />
        <span className="truncate">{settings}</span>
      </Link>
      <Background className="h-12 w-full rounded-md bg-muted px-4 text-foreground [&>svg]:size-5" />
    </div>
  );
}
