import { Link, Outlet } from "@tanstack/react-router";
import { Users } from "lucide-react";
import appIcon from "../../../docs/icon.svg";

/** Sidebar on the window background, content on a rounded page (docs/ui.md#布局). */
export function Shell() {
  return (
    <div className="flex h-dvh">
      <aside className="flex w-[228px] shrink-0 flex-col px-3 py-4">
        <div className="flex items-center gap-2.5 px-2 pb-5 font-semibold">
          <img src={appIcon} alt="" className="size-[30px]" />
          Auto Email Sender
        </div>
        <nav className="flex flex-col gap-0.5">
          <Link
            to="/"
            className="flex h-10 items-center gap-3 rounded-md px-3 text-muted-foreground hover:bg-press"
            activeProps={{ className: "bg-background font-medium text-brand-text hover:bg-background" }}
          >
            <Users className="size-[18px]" strokeWidth={1.6} />
            导师
          </Link>
        </nav>
      </aside>
      <main className="my-3 mr-3 flex min-w-0 flex-1 flex-col overflow-hidden rounded-xl bg-background">
        <Outlet />
      </main>
    </div>
  );
}
