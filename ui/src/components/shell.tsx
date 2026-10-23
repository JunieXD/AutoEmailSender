import { useQuery } from "@tanstack/react-query";
import { Link, Outlet } from "@tanstack/react-router";
import { Users } from "lucide-react";
import appIcon from "../../../docs/icon.svg";
import { startupFailure } from "@/api/app";
import { StartupFailure } from "@/components/startup-failure";

const NAV = [{ to: "/", label: "导师", icon: Users }] as const;

/**
 * Sidebar on the window background, content on a rounded page; on a phone-width window the
 * sidebar becomes a tab bar under the page (docs/ui.md#布局).
 */
export function Shell() {
  const failure = useQuery({ queryKey: ["startupFailure"], queryFn: startupFailure, staleTime: Infinity });
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
          {NAV.map(({ to, label, icon: Icon }) => (
            <Link
              key={to}
              to={to}
              className="flex h-10 items-center gap-3 rounded-md px-3 text-muted-foreground hover:bg-press"
              activeProps={{ className: "bg-background font-medium text-brand-text hover:bg-background" }}
            >
              <Icon className="size-[18px]" strokeWidth={1.6} />
              {label}
            </Link>
          ))}
        </nav>
      </aside>
      <main className="my-3 mr-3 flex min-w-0 flex-1 flex-col overflow-hidden rounded-xl bg-background phone:m-0 phone:min-h-0 phone:rounded-none">
        <Outlet />
      </main>
      <nav className="hidden auto-cols-fr grid-flow-col border-t border-border bg-background px-1 pt-1.5 pb-[calc(6px+env(safe-area-inset-bottom))] phone:grid">
        {NAV.map(({ to, label, icon: Icon }) => (
          <Link
            key={to}
            to={to}
            className="flex flex-col items-center gap-0.5 py-1 text-[11px] text-muted-foreground"
            activeProps={{ className: "text-brand-text" }}
          >
            <Icon className="size-[22px]" strokeWidth={1.6} />
            {label}
          </Link>
        ))}
      </nav>
    </div>
  );
}
