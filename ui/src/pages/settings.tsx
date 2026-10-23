import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, Outlet, useChildMatches, useNavigate } from "@tanstack/react-router";
import { getVersion } from "@tauri-apps/api/app";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { type ReactNode, useEffect } from "react";
import { dataLocation, exportLogs, openDataLocation } from "@/api/app";
import { errorMessage } from "@/api/call";
import { Button } from "@/components/ui/button";

// docs/ui.md#页面: sections in groups; only the ones that exist so far.
const GROUPS = [
  { label: "邮件", sections: [{ to: "/settings/mailboxes", label: "邮箱" }] },
  { label: "应用", sections: [{ to: "/settings/about", label: "关于和日志" }] },
] as const;

/** The `phone` breakpoint in index.css. */
const PHONE = "(width <= 760px)";

/** The group list beside the open section; on a phone, the list first and then one section. */
export function SettingsPage() {
  // A section is open unless this is the bare /settings, which on a phone is the group list.
  const open = useChildMatches({ select: (matches) => matches.some((m) => m.routeId !== "/settings/") });
  // docs/ui.md#页面: a wider window opens the first section, including when a phone-width one widens.
  const navigate = useNavigate();
  useEffect(() => {
    const phone = matchMedia(PHONE);
    const openFirst = () => {
      if (!open && !phone.matches) void navigate({ to: GROUPS[0].sections[0].to, replace: true });
    };
    openFirst();
    phone.addEventListener("change", openFirst);
    return () => phone.removeEventListener("change", openFirst);
  }, [open, navigate]);
  return (
    <div className="grid min-h-0 flex-1 grid-cols-[220px_minmax(0,1fr)] phone:grid-cols-1">
      <nav className={`flex flex-col gap-5 overflow-y-auto px-3 py-6 ${open ? "phone:hidden" : ""}`}>
        <h1 className="hidden px-3 text-[22px] font-semibold phone:block">设置</h1>
        {GROUPS.map((g) => (
          <div key={g.label} className="flex flex-col gap-0.5">
            <h2 className="px-3 pb-1 text-sm text-muted-foreground">{g.label}</h2>
            {g.sections.map((s) => (
              <Link
                key={s.to}
                to={s.to}
                className="flex h-10 items-center justify-between rounded-md px-3"
                activeProps={{ className: "bg-selected font-medium text-brand-text" }}
                inactiveProps={{ className: "hover:bg-muted" }}
              >
                {s.label}
                <ChevronRight className="hidden size-[18px] text-muted-foreground phone:block" strokeWidth={1.6} />
              </Link>
            ))}
          </div>
        ))}
      </nav>
      <div className={`min-h-0 overflow-y-auto border-l border-border phone:border-0 ${open ? "" : "phone:hidden"}`}>
        <Outlet />
      </div>
    </div>
  );
}

/** A settings section: its title, with the way back to the list on a phone. */
export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex max-w-[640px] flex-col gap-6 px-8 py-6 phone:px-4 phone:py-3">
      <header className="flex items-center gap-1">
        <Link
          to="/settings"
          aria-label="返回设置"
          className="-ml-2 hidden size-10 place-items-center rounded-md text-muted-foreground phone:grid"
        >
          <ChevronLeft className="size-[18px]" strokeWidth={1.6} />
        </Link>
        <h1 className="text-2xl font-semibold phone:text-[22px]">{title}</h1>
      </header>
      {children}
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <h2 className="font-medium">{label}</h2>
      {children}
    </div>
  );
}

export function AboutSection() {
  const version = useQuery({ queryKey: ["appVersion"], queryFn: getVersion, staleTime: Infinity });
  const location = useQuery({ queryKey: ["dataLocation"], queryFn: dataLocation, staleTime: Infinity });
  const open = useMutation({ mutationFn: openDataLocation });
  const exported = useMutation({ mutationFn: exportLogs });
  return (
    <Section title="关于和日志">
      <Row label="版本">
        <p className="text-sm text-muted-foreground">{version.data ?? " "}</p>
      </Row>
      <Row label="数据位置">
        <p className="text-sm break-all text-muted-foreground">{location.data ?? " "}</p>
        <div className="flex items-center gap-3">
          <Button variant="quiet" disabled={open.isPending} onClick={() => open.mutate()}>
            打开
          </Button>
          {open.isError && <span className="text-sm text-destructive">{errorMessage(open.error)}</span>}
        </div>
      </Row>
      <Row label="日志">
        <p className="text-sm text-muted-foreground">
          日志记下应用做过的事，用来查明问题，不含邮件正文、密码和密钥。遇到问题时导出发给开发者。
        </p>
        <div className="flex items-center gap-3">
          <Button variant="quiet" disabled={exported.isPending} onClick={() => exported.mutate()}>
            导出日志
          </Button>
          {exported.data && <span className="text-sm text-muted-foreground">已导出</span>}
          {exported.isError && <span className="text-sm text-destructive">{errorMessage(exported.error)}</span>}
        </div>
      </Row>
    </Section>
  );
}
