import { useEffect, useState } from "react";
import { Bot, Loader2, Terminal } from "lucide-react";
import {
  MODAL_BACKDROP_CLASS_NAME,
  MODAL_SURFACE_CLASS_NAME,
} from "@/components/atoms/modalStyles";
import { SelectionToggleButton } from "@/components/molecules/SelectionToggleButton";
import type { DesktopAgentSupportStatus } from "@/types/desktop";

export function AgentSupportOnboarding() {
  const api = window.autoEmailSender;
  const [status, setStatus] = useState<DesktopAgentSupportStatus | null>(null);
  const [installCodex, setInstallCodex] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    if (!api?.getAgentSupportStatus) {
      return () => {
        active = false;
      };
    }
    void api.getAgentSupportStatus().then((nextStatus) => {
      if (active) {
        setStatus(nextStatus);
      }
    }).catch(() => undefined);
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

  if (!status?.onboardingPending || status.state !== "not_enabled") {
    return null;
  }

  const codex = status.agents.find((agent) => agent.id === "codex");
  const codexDetected = codex?.detected === true;
  const canInstallCodex = codexDetected && codex.state === "not_installed";

  const dismiss = async () => {
    setWorking(true);
    setError(null);
    try {
      if (api?.dismissAgentSupportOnboarding) {
        setStatus(await api.dismissAgentSupportOnboarding());
      } else {
        setStatus({ ...status, onboardingPending: false });
      }
    } catch (dismissError) {
      setError(getErrorMessage(dismissError));
    } finally {
      setWorking(false);
    }
  };

  const enable = async () => {
    if (!api?.enableAgentSupport) {
      return;
    }
    setWorking(true);
    setError(null);
    try {
      setStatus(await api.enableAgentSupport({
        installDetectedAgents: canInstallCodex && installCodex,
      }));
    } catch (enableError) {
      setError(getErrorMessage(enableError));
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className={`${MODAL_BACKDROP_CLASS_NAME} z-[100] px-4`} role="dialog" aria-modal="true" aria-labelledby="agent-support-onboarding-title">
      <div className={`${MODAL_SURFACE_CLASS_NAME} w-full max-w-lg p-6`}>
        <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary">
          <Bot className="h-5 w-5" />
        </div>
        <h2 id="agent-support-onboarding-title" className="mt-4 text-2xl font-semibold text-stone-900">
          启用命令行与 Agent 支持？
        </h2>
        <p className="mt-3 text-sm leading-6 text-stone-600">
          让本地 Agent 查询数据、生成草稿并操作软件；发送前仍需确认。
        </p>
        <p className="mt-3 text-sm leading-6 text-stone-500">
          {canInstallCodex
            ? "已检测到 Codex，可同时完成接入。"
            : "先安装命令行，再到个人中心选择 Agent。"}
        </p>
        {canInstallCodex ? (
          <label className="mt-5 flex cursor-pointer items-start gap-3 rounded-2xl border border-primary/20 bg-primary/[0.045] px-4 py-3.5 transition hover:border-primary/35 hover:bg-primary/[0.07]">
            <SelectionToggleButton
              label="同时接入 Codex"
              selected={installCodex}
              disabled={working}
              semantics="checkbox"
              size="md"
              className="mt-0.5"
              onToggle={() => setInstallCodex((current) => !current)}
            />
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-2 text-sm font-medium text-stone-900">
                同时接入 Codex
                <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-semibold text-primary">
                  推荐
                </span>
              </span>
              <span className="mt-1 block text-xs leading-5 text-stone-500">
                安装官方 Skill，让 Codex 按你的指令操作软件。
              </span>
            </span>
          </label>
        ) : codexDetected && codex?.state === "conflict" ? (
          <div className="mt-5 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-800">
            Codex 的 Skill 目录已有其他文件，不会自动覆盖；可在个人中心查看。
          </div>
        ) : null}
        {error ? (
          <div role="alert" className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        ) : null}
        <div className="mt-6 flex flex-wrap justify-end gap-3">
          <button type="button" className="ui-btn-secondary" disabled={working} onClick={() => void dismiss()}>
            暂不启用
          </button>
          <button type="button" className="ui-btn-primary" disabled={working} onClick={() => void enable()}>
            {working ? <Loader2 className="h-4 w-4 animate-spin" /> : <Terminal className="h-4 w-4" />}
            {canInstallCodex && installCodex
              ? "启用并接入 Codex"
              : canInstallCodex
                ? "仅启用命令行"
                : "启用命令行"}
          </button>
        </div>
      </div>
    </div>
  );
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "操作失败，请稍后重试。";
}
