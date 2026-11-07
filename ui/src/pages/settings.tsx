import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Outlet, useChildMatches, useNavigate } from "@tanstack/react-router";
import { getVersion } from "@tauri-apps/api/app";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { autostartEnabled, checkUpdate, dataLocation, exportLogs, openDataLocation, setAutostart } from "@/api/app";
import { call, errorMessage } from "@/api/call";
import { ModelAction } from "@/components/ai-writing";
import { refreshReplyOutcomes } from "@/components/mentor";
import { UpdateDialog, useUpdate } from "@/components/update";
import { Button } from "@/components/ui/button";
import { Segments } from "@/components/ui/segments";
import { desktop } from "@/host";
import { LegalLink } from "@/legal";
import { savedTheme, setTheme } from "@/theme";

// docs/ui.md#页面: sections in groups; only the ones that exist so far.
const GROUPS = [
  {
    label: "个人",
    sections: [
      { to: "/settings/profile", label: "个人资料" },
      { to: "/settings/materials", label: "材料" },
      { to: "/settings/templates", label: "模板" },
      { to: "/settings/letter-style", label: "信件样式" },
    ],
  },
  {
    label: "邮件",
    sections: [
      { to: "/settings/mailboxes", label: "邮箱" },
      { to: "/settings/checks", label: "发送前检查" },
      { to: "/settings/replies", label: "回信处理" },
      { to: "/settings/tracking", label: "邮件追踪" },
    ],
  },
  { label: "AI", sections: [{ to: "/settings/models", label: "模型" }] },
  {
    label: "应用",
    sections: [
      { to: "/settings/appearance", label: "外观" },
      { to: "/settings/notifications", label: "通知" },
      { to: "/settings/background", label: "后台运行", desktopOnly: true },
      { to: "/settings/backup", label: "备份" },
      { to: "/settings/remote", label: "手机和 Agent", desktopOnly: true },
      { to: "/settings/account", label: "账号和 Plus" },
      { to: "/settings/about", label: "关于和日志" },
    ],
  },
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
            {g.sections
              .filter((s) => desktop || !("desktopOnly" in s))
              .map((s) => (
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

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <h2 className="font-medium">{label}</h2>
      {children}
    </div>
  );
}

export function AppearanceSection() {
  const [theme, setShown] = useState(savedTheme);
  return (
    <Section title="外观">
      <Row label="颜色">
        <Segments
          label="颜色"
          className="self-start"
          value={theme}
          options={[
            ["system", "跟随系统"],
            ["light", "浅色"],
            ["dark", "深色"],
          ]}
          onChange={(t) => {
            setTheme(t);
            setShown(t);
          }}
        />
      </Row>
    </Section>
  );
}

export function AboutSection() {
  const version = useQuery({ queryKey: ["appVersion"], queryFn: getVersion, staleTime: Infinity });
  return (
    <Section title="关于和日志">
      <Row label="版本">
        <p className="text-sm text-muted-foreground">{version.data ?? " "}</p>
        {desktop && <CheckUpdate />}
      </Row>
      {desktop && <ComputerSide />}
      <Row label="条款">
        <p className="flex flex-wrap text-sm">
          <LegalLink legal="terms" />
          <LegalLink legal="privacy" />
          <LegalLink legal="plus" />
        </p>
      </Row>
    </Section>
  );
}

/** Where the data and logs are, which only the computer can open or save. */
function ComputerSide() {
  const location = useQuery({ queryKey: ["dataLocation"], queryFn: dataLocation, staleTime: Infinity });
  const open = useMutation({ mutationFn: openDataLocation });
  const exported = useMutation({ mutationFn: exportLogs });
  return (
    <>
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
    </>
  );
}

/** A newer version opens the same dialog as the sidebar entry. */
function CheckUpdate() {
  const queryClient = useQueryClient();
  const status = useUpdate().data;
  const [open, setOpen] = useState(false);
  const check = useMutation({
    mutationFn: checkUpdate,
    onSuccess: (s) => {
      queryClient.setQueryData(["updateStatus"], s);
      setOpen(s.kind !== "current");
    },
  });
  return (
    <div className="flex items-center gap-3">
      <Button variant="quiet" disabled={check.isPending} onClick={() => check.mutate()}>
        {check.isPending ? "正在检查…" : "检查更新"}
      </Button>
      {check.isSuccess && status?.kind === "current" && (
        <span className="text-sm text-muted-foreground">已是最新版本</span>
      )}
      {check.isError && <span className="text-sm text-destructive">{errorMessage(check.error)}</span>}
      {open && status && status.kind !== "current" && <UpdateDialog status={status} onClose={() => setOpen(false)} />}
    </div>
  );
}

export function NotificationsSection() {
  return (
    <Section title="通知">
      <Toggle
        get="getReplyNotifications"
        set="setReplyNotifications"
        label="新回信时发系统通知"
        description="应用不在前台时，导师回信会弹出系统通知。"
      />
      <Toggle
        get="getBackgroundNotifications"
        set="setBackgroundNotifications"
        label="后台工作做完时通知"
        description="应用不在前台时，匹配分析等后台工作完成后弹出系统通知；在前台时在内容页提醒。"
      />
      <Toggle
        get="getTodoReminders"
        set="setTodoReminders"
        label="待办提醒"
        description="有截止日期的待办，在截止前一天上午 9:00 弹出系统通知。"
      />
    </Section>
  );
}

/** What docs/design.md#联系 checks before sending; the rules always, AI 核对 when turned on. */
export function ChecksSection() {
  return (
    <Section title="发送前检查">
      <div className="text-sm text-muted-foreground">
        <p>草稿写好或修改后自动检查，问题列在正文下方，发送或批准时再提醒一次，可以忽略。总会检查：</p>
        <ul className="mt-1.5 list-disc pl-5">
          <li>占位符没有替换，或留着“【请补充：……】”这样的标记</li>
          <li>正文提到附件，却没有添加附件</li>
          <li>出现了任务中另一位导师的姓名</li>
          <li>正文里没有称呼导师</li>
          <li>最近 7 天内已经联系过这位导师</li>
          <li>导师邮箱的域名查不到收信服务器</li>
        </ul>
      </div>
      <Toggle
        get="getAiFactCheck"
        set="setAiFactCheck"
        label="AI 核对"
        description="用过 AI 起草或改写的信，AI 会找出其中你的经历、成果和数字在个人资料和写信参考材料里找不到依据的地方。这会把信、个人资料里的姓名和意向研究方向、写信参考材料发给模型服务商，每封多调用一次模型。"
      />
    </Section>
  );
}

function Toggle({
  get,
  set,
  label,
  description,
}: {
  get: "getReplyNotifications" | "getBackgroundNotifications" | "getTodoReminders" | "getAiFactCheck";
  set: "setReplyNotifications" | "setBackgroundNotifications" | "setTodoReminders" | "setAiFactCheck";
  label: string;
  description: string;
}) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: [get], queryFn: () => call(get, {}) });
  const save = useMutation({
    mutationFn: (on: boolean) => call(set, { on }),
    onSuccess: async (_, on) => {
      await queryClient.cancelQueries({ queryKey: [get] });
      queryClient.setQueryData([get], on);
    },
  });
  return (
    <div>
      <label className="flex items-center justify-between gap-4">
        <span>{label}</span>
        <SwitchButton
          label={label}
          checked={query.data ?? false}
          disabled={query.data === undefined || save.isPending}
          onClick={() => save.mutate(!query.data)}
        />
      </label>
      <p className="mt-2 text-sm text-muted-foreground">{description}</p>
      {query.error && <p className="mt-2 text-sm text-destructive">设置未能加载：{errorMessage(query.error)}</p>}
      {save.error && <p className="mt-2 text-sm text-destructive">设置未能保存：{errorMessage(save.error)}</p>}
    </div>
  );
}

export function SwitchButton({
  label,
  checked,
  disabled,
  onClick,
}: {
  label: string;
  checked: boolean;
  disabled: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-label={label}
      aria-checked={checked}
      disabled={disabled}
      onClick={onClick}
      className="group flex h-6 w-10 shrink-0 items-center rounded-full bg-input p-0.5 aria-checked:bg-primary disabled:opacity-50"
    >
      <span className="size-5 rounded-full bg-white shadow-sm transition-transform group-aria-checked:translate-x-4" />
    </button>
  );
}

/** Replies the AI reads as they come (docs/ui.md#交互); turning either on asks the provider consent first. */
export function ReplyProcessingSection() {
  return (
    <Section title="回信处理">
      <AiSwitch
        get="getAiReplyOutcome"
        set="setAiReplyOutcome"
        label="AI 标记回信结果"
        description="开启后，导师的回信（包括已经收到的）会发给默认模型的服务商，标出有意向、无名额、拒绝或无法判断。不开也可以在往来里自己标。"
      />
      <AiSwitch
        get="getAiReplyTodos"
        set="setAiReplyTodos"
        label="回信待办"
        description="开启后，导师的回信会发给默认模型的服务商，找出导师要你做的事和截止时间，列进待办；已经收到的只找最近 14 天的。不开也可以在往来里自己添加待办。"
      />
    </Section>
  );
}

function AiSwitch({
  get,
  set,
  label,
  description,
}: {
  get: "getAiReplyOutcome" | "getAiReplyTodos";
  set: "setAiReplyOutcome" | "setAiReplyTodos";
  label: string;
  description: string;
}) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: [get], queryFn: () => call(get, {}) });
  const save = useMutation({
    mutationFn: (enabled: boolean) => call(set, { enabled }),
    onSuccess: async (_, enabled) => {
      await queryClient.cancelQueries({ queryKey: [get] });
      queryClient.setQueryData([get], enabled);
      refreshReplyOutcomes(queryClient);
    },
  });
  const busy = query.data === undefined || save.isPending;
  return (
    <div>
      <div className="flex items-center justify-between gap-4">
        <span>{label}</span>
        {query.data ? (
          <SwitchButton label={label} checked disabled={busy} onClick={() => save.mutate(false)} />
        ) : (
          <ModelAction
            disabled={busy}
            onWrite={() => save.mutate(true)}
            renderTrigger={(props) => <SwitchButton label={label} checked={false} {...props} />}
          />
        )}
      </div>
      <p className="mt-2 text-sm text-muted-foreground">{description}</p>
      {query.error && <p className="mt-2 text-sm text-destructive">设置未能加载：{errorMessage(query.error)}</p>}
      {save.error && <p className="mt-2 text-sm text-destructive">设置未能保存：{errorMessage(save.error)}</p>}
    </div>
  );
}

/** Whether the app starts with the computer; the system's login items keep it (docs/ui.md#交互). */
export function BackgroundSection() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["autostartEnabled"], queryFn: autostartEnabled });
  const save = useMutation({
    mutationFn: setAutostart,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["autostartEnabled"] }),
  });
  return (
    <Section title="后台运行">
      <div>
        <label className="flex items-center justify-between gap-4">
          <span>开机时自动启动</span>
          <SwitchButton
            label="开机时自动启动"
            checked={query.data ?? false}
            disabled={query.data === undefined || save.isPending}
            onClick={() => save.mutate(!query.data)}
          />
        </label>
        <p className="mt-2 text-sm text-muted-foreground">开机后在后台运行，不打开窗口，定时发送和收信照常进行。</p>
        {query.error && (
          <p className="mt-2 text-sm text-destructive">开机启动的设置未能读取：{errorMessage(query.error)}</p>
        )}
        {save.error && <p className="mt-2 text-sm text-destructive">{errorMessage(save.error)}</p>}
      </div>
      <p className="text-sm text-muted-foreground">
        关闭窗口后，应用也留在后台继续工作，可以从托盘或菜单栏里的图标打开或退出。
      </p>
    </Section>
  );
}
