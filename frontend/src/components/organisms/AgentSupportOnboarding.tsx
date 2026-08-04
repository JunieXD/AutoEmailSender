import { useEffect, useState } from "react";
import { Bot, Loader2, Terminal } from "lucide-react";
import type { DesktopAgentSupportStatus } from "@/types/desktop";

export function AgentSupportOnboarding() {
  const api = window.autoEmailSender;
  const [status, setStatus] = useState<DesktopAgentSupportStatus | null>(null);
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
      setStatus(await api.enableAgentSupport());
    } catch (enableError) {
      setError(getErrorMessage(enableError));
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-stone-950/35 px-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="agent-support-onboarding-title">
      <div className="w-full max-w-lg rounded-3xl border border-stone-200 bg-white p-6 shadow-2xl">
        <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary">
          <Bot className="h-5 w-5" />
        </div>
        <h2 id="agent-support-onboarding-title" className="mt-4 text-2xl font-semibold text-stone-900">
          启用命令行与 Agent 支持？
        </h2>
        <p className="mt-3 text-sm leading-6 text-stone-600">
          启用后，Codex、Claude Code、Cursor 等本地 Agent 可以按照你的要求查询数据、生成草稿并操作 Auto Email Sender。真实发送仍必须先展示计划并得到你的明确确认。
        </p>
        <p className="mt-3 text-sm leading-6 text-stone-500">
          软件会先安装命令行。随后可在个人中心选择要接入的 Agent；已安装的官方使用说明会随软件升级自动更新。
        </p>
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
            启用命令行
          </button>
        </div>
      </div>
    </div>
  );
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "操作失败，请稍后重试。";
}
