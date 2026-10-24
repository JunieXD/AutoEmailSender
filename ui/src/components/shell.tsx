import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Outlet } from "@tanstack/react-router";
import { listen } from "@tauri-apps/api/event";
import { MessagesSquare, Settings, Users } from "lucide-react";
import { useEffect } from "react";
import appIcon from "../../../docs/icon.svg";
import { call } from "@/api/call";
import { startupFailure } from "@/api/app";
import { refreshMentors } from "@/components/mentor";
import { StartupFailure } from "@/components/startup-failure";

const NAV = [
  { to: "/mentors", label: "导师", icon: Users },
  { to: "/conversations", label: "往来", icon: MessagesSquare },
] as const;

// docs/ui.md#布局: settings open from the account row at the bottom of the sidebar; until there are
// accounts, the row is settings itself.
const SETTINGS = { to: "/settings", label: "设置", icon: Settings } as const;

/**
 * Sidebar on the window background, content on a rounded page; on a phone-width window the
 * sidebar becomes a tab bar under the page (docs/ui.md#布局).
 */
export function Shell() {
  const failure = useQuery({ queryKey: ["startupFailure"], queryFn: startupFailure, staleTime: Infinity });
  const queryClient = useQueryClient();
  const unread = useQuery({
    queryKey: ["unreadCount"],
    queryFn: () => call("unreadCount", {}),
    enabled: failure.isSuccess && !failure.data,
  });
  const count = unread.data ?? 0;
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
            <NavLink key={item.to} {...item} count={item.to === "/conversations" ? count : 0} />
          ))}
        </nav>
        <div className="grow" />
        <NavLink {...SETTINGS} />
      </aside>
      <main className="my-3 mr-3 flex min-w-0 flex-1 flex-col overflow-hidden rounded-xl bg-background phone:m-0 phone:min-h-0 phone:rounded-none">
        <Outlet />
      </main>
      <nav className="hidden auto-cols-fr grid-flow-col border-t border-border bg-background px-1 py-1.5 phone:grid">
        {[...NAV, SETTINGS].map(({ to, label, icon: Icon }) => (
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
              {to === "/conversations" && count > 0 && (
                <span className="min-w-5 rounded-full bg-unread px-1.5 text-center text-xs leading-5 text-white">
                  {count > 99 ? "99+" : count}
                </span>
              )}
            </span>
          </Link>
        ))}
      </nav>
    </div>
  );
}

function NavLink({ to, label, icon: Icon, count = 0 }: ((typeof NAV)[number] | typeof SETTINGS) & { count?: number }) {
  return (
    <Link
      to={to}
      className="flex h-10 items-center gap-3 rounded-md px-3"
      activeProps={{ className: "bg-background font-medium text-brand-text" }}
      inactiveProps={{ className: "text-muted-foreground hover:bg-press" }}
    >
      <Icon className="size-[18px]" strokeWidth={1.6} />
      {label}
      {count > 0 && (
        <span className="ml-auto min-w-5 rounded-full bg-unread px-1.5 text-center text-xs leading-5 text-white">
          {count > 99 ? "99+" : count}
        </span>
      )}
    </Link>
  );
}
