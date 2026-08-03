import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import {
  Bot,
  CheckCircle2,
  ChevronDown,
  Loader2,
  RefreshCw,
  Terminal,
  Wrench,
} from "lucide-react";
import type {
  DesktopAgentSupportState,
  DesktopAgentSupportStatus,
} from "@/types/desktop";

const statusLabels: Record<DesktopAgentSupportState, string> = {
  not_enabled: "未启用",
  installing: "安装中",
  enabled: "已启用",
  needs_repair: "需要修复",
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

const unsupportedStatus: DesktopAgentSupportStatus = {
  supported: false,
  state: "unsupported",
  message: "命令行与 Agent 支持仅在安装后的 Windows 或 Apple 芯片 Mac 桌面版中可用。",
  onboardingPending: false,
  cliCommand: "auto-email-sender",
  cliPath: "",
  skillPath: "",
  appVersion: "",
  requiresAgentRestart: false,
};

export function AgentSupportCard() {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<DesktopAgentSupportStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [action, setAction] = useState<"enable" | "repair" | "disable" | "refresh" | null>(null);
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

  const runAction = async (
    nextAction: "enable" | "repair" | "disable" | "refresh",
  ) => {
    if (!api) {
      return;
    }
    if (nextAction === "disable" && !window.confirm("确认关闭命令行与 Agent 支持？已打开的 Agent 对话可能需要重启。")) {
      return;
    }
    const operation = {
      enable: api.enableAgentSupport,
      repair: api.repairAgentSupport,
      disable: api.disableAgentSupport,
      refresh: api.getAgentSupportStatus,
    }[nextAction];
    if (!operation) {
      setError("当前桌面版本不支持此操作，请升级软件。 ");
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

  return (
    <section className="min-w-0 overflow-hidden rounded-2xl border border-stone-200 bg-white shadow-sm">
      <button
        type="button"
        aria-expanded={open}
        aria-controls="agent-support-card-content"
        onClick={() => setOpen((value) => !value)}
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

      {open ? (
        <div id="agent-support-card-content" className="border-t border-stone-100 px-6 pb-6">
          <div className="mt-5 grid gap-4 lg:grid-cols-[1.1fr,0.9fr]">
            <div className="rounded-2xl border border-stone-200 bg-[#fcfbf8] p-5">
              <div className="flex items-center gap-2 text-sm font-semibold text-stone-900">
                <Bot className="h-4 w-4 text-primary" />
                它能做什么
              </div>
              <p className="mt-3 text-sm leading-6 text-stone-600">
                例如，你可以让 Agent 读取全部回信，自行找出“意思是没有名额”的导师，选择模板和附件生成草稿。真正发送前，它必须先展示一次性发送计划，并等你明确确认。
              </p>
              <p className="mt-3 text-sm leading-6 text-stone-500">
                这不是只支持某一个例子，也不绑定某个 Agent；Agent 会根据当前 CLI 提供的能力组合查询、草稿、计划和发送操作。
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
                    告诉 Agent 每个命令能做什么、怎样查询，以及发送前必须遵守的确认规则。每次 CLI 响应也会提醒 Agent 阅读对应说明。
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
                启用
              </button>
            ) : null}
            {displayStatus.state === "needs_repair" ? (
              <button type="button" className="ui-btn-primary" disabled={busy} onClick={() => void runAction("repair")}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wrench className="h-4 w-4" />}
                修复
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
              <button type="button" className="ui-btn-danger" disabled={busy} onClick={() => void runAction("disable")}>
                关闭支持
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "操作失败，请稍后重试。";
}
