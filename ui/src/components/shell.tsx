import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Outlet } from "@tanstack/react-router";
import { listen } from "@tauri-apps/api/event";
import { MessagesSquare, Settings, Users, ListTodo, LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "cn";
import appIcon from "../../../docs/icon.svg";
import { call, errorMessage } from "@/api/call";
import { startupFailure } from "@/api/app";
import type { MatchFinished, DraftsFinished } from "@/api/types";
import { refreshTasks } from "@/pages/tasks";
import { refreshMentors } from "@/components/mentor";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { StartupFailure } from "@/components/startup-failure";

const NAV = [
  { to: "/mentors", label: "导师", icon: Users },
  { to: "/conversations", label: "往来", icon: MessagesSquare },
  { to: "/tasks", label: "任务", icon: ListTodo },
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
    const stop = listen("matching-changed", () => refreshMentors(queryClient));
    const done = listen<MatchFinished>("matching-finished", ({ payload: p }) => {
      refreshMentors(queryClient);
      setCompleted(`匹配度分析完成：分析了 ${p.analyzed} 位${p.failed > 0 ? `，失败 ${p.failed} 位` : ""}`);
    });
    const tasksChanged = listen("tasks-changed", () => refreshTasks(queryClient));
    const draftsDone = listen<DraftsFinished>("drafts-finished", ({ payload: p }) => {
      refreshTasks(queryClient);
      setCompleted(`任务“${p.name}”的草稿写好了：${p.generated} 封，失败 ${p.failed} 封`);
    });
    return () => {
      void tasksChanged.then((f) => f());
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
        {[...NAV, MORE].map(({ to, label, icon: Icon }) => (
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

function Background() {
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
  const active = tasks.data?.filter((t) => t.progress.generating > 0) ?? [];
  const pending =
    tasks.data?.filter((t) => t.state === "paused" || t.progress.generationFailed > 0 || t.progress.failed > 0) ?? [];
  const count = active.length + (matching.data && matching.data.remaining > 0 ? 1 : 0);
  return (
    <Popover>
      <PopoverTrigger
        render={
          <button
            className={`mb-1 flex h-10 items-center gap-2 rounded-md px-3 text-sm text-muted-foreground hover:bg-press `}
          />
        }
      >
        <LoaderCircle className={`size-4 ${count > 0 ? "animate-spin" : ""}`} />
        后台{count > 0 && <span>{count}</span>}
        {pending.length > 0 && <span className="text-warning">{pending.length}</span>}
      </PopoverTrigger>
      <PopoverContent side="top" align="start" className="max-h-96 w-80 space-y-3 overflow-y-auto p-4 text-sm">
        <h2 className="font-medium">后台工作</h2>
        {pending.length > 0 && (
          <div>
            <h3 className="mb-2 text-warning">待处理</h3>
            {pending.map((t) => (
              <Link key={t.id} to="/tasks/$id" params={{ id: t.id }} className="block py-2">
                {t.name}
                <span className="block text-xs text-muted-foreground">
                  {t.pauseReason ?? `${t.progress.generationFailed} 封生成失败，${t.progress.failed} 封发送失败`}
                </span>
              </Link>
            ))}
          </div>
        )}
        {active.length > 0 && (
          <div>
            <h3 className="text-muted-foreground">生成草稿</h3>
            {active.map((t) => (
              <Link key={t.id} to="/tasks/$id" params={{ id: t.id }} className="block py-2">
                {t.name} · 还剩 {t.progress.generating} 封
              </Link>
            ))}
          </div>
        )}
        {matching.data && matching.data.remaining > 0 && (
          <Link to="/mentors" className="block">
            匹配分析 · 还剩 {matching.data.remaining} 位
          </Link>
        )}
        {count === 0 && pending.length === 0 && <p className="text-muted-foreground">当前没有后台工作</p>}
        {(tasks.error ?? matching.error) && (
          <p className="text-destructive">{errorMessage(tasks.error ?? matching.error)}</p>
        )}
      </PopoverContent>
    </Popover>
  );
}

export function MorePage() {
  return (
    <div className="p-5">
      <h1 className="mb-5 text-2xl font-semibold">更多</h1>
      <Link to="/settings" className="mb-3 flex h-12 items-center gap-3 rounded-md bg-muted px-4">
        <Settings className="size-5" />
        设置
      </Link>
      <Background />
    </div>
  );
}
