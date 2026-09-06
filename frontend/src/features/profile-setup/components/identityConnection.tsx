import { EmailDeliveryFailureDetails } from "@/components/molecules/EmailDeliveryFailureDetails";
import { IdentityConnectionTestSummary } from "@/features/profile-setup/model/formControls";
import { PROFILE_HELP_LINKS } from "@/lib/helpLinks";
import { Loader2 } from "lucide-react";
import { ContextualHelpLink } from "./formControls";

export const IdentityConnectionCard = ({
  testingIdentityConnection,
  lastResult,
  onTestSmtp,
  onTestImap,
}: {
  testingIdentityConnection: "smtp" | "imap" | null;
  lastResult: IdentityConnectionTestSummary | null;
  onTestSmtp: () => void;
  onTestImap: () => void;
}) => (
  <div className="rounded-[28px] border border-stone-200 bg-[linear-gradient(135deg,#fffdfa,#fff9f2_52%,#fff5ea)] p-5 shadow-sm shadow-stone-200/70">
    <div className="flex flex-wrap justify-between items-center gap-4">
      <div className="space-y-2">
        <div className="text-sm font-medium text-stone-900">邮箱连接测试</div>
      </div>
      <div className="flex flex-wrap gap-3">
        <button
          type="button"
          onClick={onTestSmtp}
          disabled={testingIdentityConnection !== null}
          className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
        >
          {testingIdentityConnection === "smtp" ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : null}
          测试 SMTP
        </button>
        <button
          type="button"
          onClick={onTestImap}
          disabled={testingIdentityConnection !== null}
          className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
        >
          {testingIdentityConnection === "imap" ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : null}
          测试 IMAP
        </button>
      </div>
    </div>
    {lastResult ? (
      <div className="mt-4 rounded-2xl border border-stone-200/80 bg-white/80 px-4 py-3 text-sm text-stone-700">
        <div className="font-medium text-stone-900">
          上次测试：{lastResult.kind.toUpperCase()}
          {lastResult.status === "success" ? " 成功" : " 失败"}
        </div>
        {lastResult.kind === "smtp" && lastResult.status === "error" ? (
          <EmailDeliveryFailureDetails
            possibleCause={lastResult.possibleCause}
            rawError={lastResult.message}
          />
        ) : (
          <div className="mt-1 whitespace-pre-wrap break-words text-stone-600">
            {lastResult.message}
          </div>
        )}
        {lastResult.status === "error" ? (
          <div className="mt-3 border-t border-stone-200 pt-3">
            <ContextualHelpLink
              href={PROFILE_HELP_LINKS.mailAuthorization}
              tone="surface"
            >
              按邮箱配置教程逐项检查
            </ContextualHelpLink>
          </div>
        ) : null}
      </div>
    ) : null}
  </div>
);
