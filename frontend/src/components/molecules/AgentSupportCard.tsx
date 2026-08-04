import { useEffect, useMemo, useState, type TransitionEvent } from "react";
import clsx from "clsx";
import {
  Bot,
  CheckCircle2,
  ChevronDown,
  Loader2,
  RefreshCw,
  Search,
  Terminal,
  Wrench,
} from "lucide-react";
import { ConfirmDialog } from "@/components/atoms/ConfirmDialog";
import type {
  DesktopAgentIntegrationStatus,
  DesktopAgentSupportState,
  DesktopAgentSupportStatus,
} from "@/types/desktop";

const statusLabels: Record<DesktopAgentSupportState, string> = {
  not_enabled: "未启用",
  installing: "安装中",
  enabled: "已启用",
  needs_repair: "需要更新",
  updating: "更新中",
  unsupported: "不支持",
};

const statusStyles: Record<DesktopAgentSupportState, string> = {
  not_enabled: "border-stone-200 bg-stone-50 text-stone-600",
  installing: "border-blue-200 bg-blue-50 text-blue-700",
  enabled: "border-emerald-200 bg-emerald-50 text-emerald-700",
  needs_repair: "border-amber-200 bg-amber-50 text-amber-700",
  updating: "border-blue-200 bg-blue-50 text-blue-700",
  unsupported: "border-stone-200 bg-stone-100 text-stone-500",
};

const agentStateLabels: Record<DesktopAgentIntegrationStatus["state"], string> = {
  not_installed: "未安装",
  installed: "已安装",
  needs_update: "需更新",
  conflict: "未接管",
  available_via_shared: "共享可用",
};

const agentStateStyles: Record<DesktopAgentIntegrationStatus["state"], string> = {
  not_installed: "border-stone-200 bg-stone-50 text-stone-600",
  installed: "border-emerald-200 bg-emerald-50 text-emerald-700",
  needs_update: "border-amber-200 bg-amber-50 text-amber-700",
  conflict: "border-amber-200 bg-amber-50 text-amber-700",
  available_via_shared: "border-blue-200 bg-blue-50 text-blue-700",
};

const unsupportedStatus: DesktopAgentSupportStatus = {
  supported: false,
  state: "unsupported",
  message: "命令行与 Agent 支持仅在安装后的 Windows 或 Apple 芯片 Mac 桌面版中可用。",
  onboardingPending: false,
  cliCommand: "auto-email-sender",
  cliPath: "",
  skillPath: "",
  agents: [],
  appVersion: "",
  requiresAgentRestart: false,
};

type MainAction = "enable" | "repair" | "disable" | "refresh";
type AgentAction = `install:${string}` | `uninstall:${string}`;

export function AgentSupportCard() {
  const [open, setOpen] = useState(false);
  const [renderContent, setRenderContent] = useState(false);
  const [status, setStatus] = useState<DesktopAgentSupportStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [action, setAction] = useState<MainAction | AgentAction | null>(null);
  const [agentQuery, setAgentQuery] = useState("");
  const [disableConfirmationOpen, setDisableConfirmationOpen] = useState(false);
  const api = window.autoEmailSender;

  useEffect(() => {
    let active = true;
    if (!api?.getAgentSupportStatus) {
      setStatus(unsupportedStatus);
      return () => {
        active = false;
      };
    }
    void api.getAgentSupportStatus()
      .then((nextStatus) => {
        if (active) {
          setStatus(nextStatus);
        }
      })
      .catch((loadError: unknown) => {
        if (active) {
          setStatus(unsupportedStatus);
          setError(getErrorMessage(loadError));
        }
      });
    const unsubscribe = api.onAgentSupportStatus?.((nextStatus) => {
      if (active) {
        setStatus(nextStatus);
      }
    });
    return () => {
      active = false;
      unsubscribe?.();
    };
  }, [api]);

  const displayStatus = status ?? {
    ...unsupportedStatus,
    state: "updating" as const,
    message: "正在检查安装状态…",
  };
  const busy = action !== null || displayStatus.state === "installing" || displayStatus.state === "updating";
  const summary = useMemo(
    () => statusLabels[displayStatus.state],
    [displayStatus.state],
  );
  const visibleAgents = useMemo(() => {
    const normalizedQuery = agentQuery.trim().toLowerCase();
    if (!normalizedQuery) {
      return displayStatus.agents;
    }
    return displayStatus.agents.filter((agent) =>
      `${agent.name} ${agent.id}`.toLowerCase().includes(normalizedQuery),
    );
  }, [agentQuery, displayStatus.agents]);
  const canManageAgents = displayStatus.state === "enabled";

  const toggleOpen = () => {
    setOpen((current) => {
      const next = !current;
      if (next) {
        setRenderContent(true);
      }
      return next;
    });
  };

  const handleContentTransitionEnd = (event: TransitionEvent<HTMLDivElement>) => {
    if (open || event.propertyName !== "grid-template-rows") {
      return;
    }
    setRenderContent(false);
  };

  const runAction = async (nextAction: MainAction) => {
    if (!api) {
      return;
    }
    const operation = {
      enable: api.enableAgentSupport,
      repair: api.repairAgentSupport,
      disable: api.disableAgentSupport,
      refresh: api.getAgentSupportStatus,
    }[nextAction];
    if (!operation) {
      setError("当前桌面版本不支持此操作，请升级软件。");
      return;
    }
    setAction(nextAction);
    setError(null);
    try {
      setStatus(await operation());
    } catch (operationError) {
      setError(getErrorMessage(operationError));
    } finally {
      setAction(null);
    }
  };

  const runAgentAction = async (
    agent: DesktopAgentIntegrationStatus,
    operation: "install" | "uninstall",
  ) => {
    const apiOperation = operation === "install" ? api?.installAgentSkill : api?.uninstallAgentSkill;
    if (!apiOperation) {
      setError("当前桌面版本不支持此操作，请升级软件。");
      return;
    }
    const nextAction: AgentAction = `${operation}:${agent.id}`;
    setAction(nextAction);
    setError(null);
    try {
      setStatus(await apiOperation(agent.id));
    } catch (operationError) {
      setError(getErrorMessage(operationError));
    } finally {
      setAction(null);
    }
  };

  return (
    <section className="min-w-0 overflow-hidden rounded-2xl border border-stone-200 bg-white shadow-sm">
      <button
        type="button"
        aria-expanded={open}
        aria-controls="agent-support-card-content"
        onClick={toggleOpen}
        className={clsx(
          "collapsible-card-toggle flex w-full items-center justify-between gap-4 px-6 py-5 text-left transition hover:bg-stone-50 active:bg-stone-50",
          open ? "rounded-t-2xl" : "rounded-2xl",
        )}
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold text-stone-900">命令行与 Agent</h2>
            <span className={clsx("rounded-full border px-3 py-1.5 text-xs", statusStyles[displayStatus.state])}>
              {summary}
            </span>
          </div>
          <p className="mt-2 text-sm leading-6 text-stone-600">
            让 Codex、Claude Code、Cursor 等本地 Agent 按你的自然语言要求操作软件。
          </p>
        </div>
        <ChevronDown
          className={clsx(
            "h-5 w-5 shrink-0 text-stone-500 transition-transform",
            open && "rotate-180",
          )}
        />
      </button>

      {renderContent ? (
        <div
          id="agent-support-card-content"
          data-state={open ? "open" : "closed"}
          onTransitionEnd={handleContentTransitionEnd}
          className="collapsible-card-content"
        >
          <div className="collapsible-card-body min-h-0 px-6">
            <div className="mt-5 grid gap-4 lg:grid-cols-[1.1fr,0.9fr]">
              <div className="rounded-2xl border border-stone-200 bg-[#fcfbf8] p-5">
                <div className="flex items-center gap-2 text-sm font-semibold text-stone-900">
                  <Bot className="h-4 w-4 text-primary" />
                  它能做什么
                </div>
                <p className="mt-3 text-sm leading-6 text-stone-600">
                  举例来说，你可以让 Agent 读取全部回信，找出回信中表示没名额的导师，选择模板和附件生成草稿再次发送邮件。真正发送前，它会先展示一次性发送计划，并等你明确确认。
                </p>
                <p className="mt-3 text-sm leading-6 text-stone-500">
                  Agent 可以根据当前 CLI 提供的能力操控软件。
                </p>
              </div>

              <div className="rounded-2xl border border-stone-200 bg-white p-5">
                <div className="flex items-center gap-2 text-sm font-semibold text-stone-900">
                  <Terminal className="h-4 w-4 text-primary" />
                  安装内容
                </div>
                <dl className="mt-3 space-y-3 text-sm">
                  <div>
                    <dt className="text-stone-500">命令</dt>
                    <dd className="mt-1 font-mono text-xs text-stone-800">{displayStatus.cliCommand}</dd>
                  </div>
                  <div>
                    <dt className="text-stone-500">Agent 使用说明（Skill）</dt>
                    <dd className="mt-1 text-xs leading-5 text-stone-700">
                      在下方选择 Agent 后安装。软件升级时，已安装的官方 Skill 会自动更新。
                    </dd>
                  </div>
                </dl>
              </div>
            </div>

            <div className={clsx(
              "mt-4 rounded-2xl border px-4 py-3 text-sm leading-6",
              displayStatus.state === "needs_repair"
                ? "border-amber-200 bg-amber-50 text-amber-800"
                : displayStatus.state === "enabled"
                  ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                  : "border-stone-200 bg-stone-50 text-stone-600",
            )}>
              <div className="flex items-start gap-2">
                {busy ? (
                  <Loader2 className="mt-1 h-4 w-4 shrink-0 animate-spin" />
                ) : displayStatus.state === "enabled" ? (
                  <CheckCircle2 className="mt-1 h-4 w-4 shrink-0" />
                ) : displayStatus.state === "needs_repair" ? (
                  <Wrench className="mt-1 h-4 w-4 shrink-0" />
                ) : null}
                <span>{displayStatus.message}</span>
              </div>
            </div>

            <div className="mt-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold text-stone-900">Agent 接入</h3>
                  <p className="mt-1 text-xs leading-5 text-stone-500">选择要让其自动读取官方 Skill 的 Agent。</p>
                </div>
                <label className="relative block w-full sm:w-56">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
                  <input
                    type="search"
                    value={agentQuery}
                    onChange={(event) => setAgentQuery(event.target.value)}
                    placeholder="搜索 Agent"
                    aria-label="搜索 Agent"
                    className="h-9 w-full rounded-lg border border-stone-200 bg-white pl-9 pr-3 text-sm text-stone-800 outline-none transition placeholder:text-stone-400 focus:border-primary focus:ring-2 focus:ring-primary/15"
                  />
                </label>
              </div>

              <div className="mt-3 divide-y divide-stone-100 rounded-lg border border-stone-200 px-4">
                {visibleAgents.map((agent) => {
                  const isAgentBusy = action === `install:${agent.id}` || action === `uninstall:${agent.id}`;
                  return (
                    <div key={agent.id} className="flex min-w-0 items-center gap-3 py-3">
                      <Bot className="h-4 w-4 shrink-0 text-stone-400" />
                      <div className="min-w-0 flex-1">
                        <div className="flex min-w-0 items-center gap-2">
                          <span className="truncate text-sm font-medium text-stone-900">{agent.name}</span>
                          <span className={clsx("shrink-0 rounded-full border px-2 py-0.5 text-[11px]", agentStateStyles[agent.state])}>
                            {agentStateLabels[agent.state]}
                          </span>
                        </div>
                        <p className="truncate text-xs leading-5 text-stone-500" title={agent.message}>{agent.message}</p>
                      </div>
                      <AgentActionButton
                        agent={agent}
                        disabled={busy || !canManageAgents}
                        busy={isAgentBusy}
                        onInstall={() => void runAgentAction(agent, "install")}
                        onUninstall={() => void runAgentAction(agent, "uninstall")}
                      />
                    </div>
                  );
                })}
                {visibleAgents.length === 0 ? (
                  <p className="py-4 text-sm text-stone-500">没有找到匹配的 Agent。</p>
                ) : null}
              </div>
            </div>

            {error ? (
              <div role="alert" className="mt-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                {error}
              </div>
            ) : null}

            {displayStatus.state === "enabled" && displayStatus.requiresAgentRestart ? (
              <p className="mt-3 text-xs leading-5 text-stone-500">
                如果 Agent 在启用前已经打开，请新建一个 Agent 对话或重启 Agent，让新的 PATH 和 Skill 生效。
              </p>
            ) : null}

            <div className="mt-5 flex flex-wrap gap-3">
              {displayStatus.state === "not_enabled" ? (
                <button type="button" className="ui-btn-primary" disabled={busy} onClick={() => void runAction("enable")}>
                  {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Terminal className="h-4 w-4" />}
                  启用命令行
                </button>
              ) : null}
              {displayStatus.state === "needs_repair" ? (
                <button type="button" className="ui-btn-primary" disabled={busy} onClick={() => void runAction("repair")}>
                  {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wrench className="h-4 w-4" />}
                  重新安装
                </button>
              ) : null}
              {displayStatus.state === "enabled" ? (
                <button type="button" className="ui-btn-secondary" disabled={busy} onClick={() => void runAction("repair")}>
                  <Wrench className="h-4 w-4" />
                  重新安装
                </button>
              ) : null}
              {displayStatus.supported ? (
                <button type="button" className="ui-btn-secondary" disabled={busy} onClick={() => void runAction("refresh")}>
                  <RefreshCw className={clsx("h-4 w-4", action === "refresh" && "animate-spin")} />
                  检查状态
                </button>
              ) : null}
              {displayStatus.state === "enabled" || displayStatus.state === "needs_repair" ? (
                <button
                  type="button"
                  className="ui-btn-danger"
                  disabled={busy}
                  onClick={() => setDisableConfirmationOpen(true)}
                >
                  关闭支持
                </button>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
      <ConfirmDialog
        open={disableConfirmationOpen}
        title="关闭命令行与 Agent 支持？"
        description="这会移除命令行工具，并卸载全部已安装的官方 Agent 使用说明。已打开的 Agent 对话可能需要新建或重启后才会停止使用它。"
        confirmLabel="确认关闭"
        tone="danger"
        onCancel={() => setDisableConfirmationOpen(false)}
        onConfirm={() => {
          setDisableConfirmationOpen(false);
          void runAction("disable");
        }}
      />
    </section>
  );
}

function AgentActionButton({
  agent,
  disabled,
  busy,
  onInstall,
  onUninstall,
}: {
  agent: DesktopAgentIntegrationStatus;
  disabled: boolean;
  busy: boolean;
  onInstall: () => void;
  onUninstall: () => void;
}) {
  if (agent.state === "conflict") {
    return null;
  }
  if (agent.state === "installed") {
    return (
      <button type="button" className="ui-btn-secondary shrink-0" disabled={disabled} onClick={onUninstall}>
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
        卸载
      </button>
    );
  }
  const label = agent.state === "needs_update" ? "更新" : agent.state === "available_via_shared" ? "单独安装" : "安装";
  return (
    <button type="button" className="ui-btn-secondary shrink-0" disabled={disabled} onClick={onInstall}>
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
      {label}
    </button>
  );
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "操作失败，请稍后重试。";
}
