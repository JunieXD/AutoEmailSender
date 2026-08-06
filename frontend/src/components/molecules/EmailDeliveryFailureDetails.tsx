interface EmailDeliveryFailureDetailsProps {
  possibleCause?: string | null;
  rawError?: string | null;
}

const UNKNOWN_CAUSE =
  "暂未能判断具体原因。请根据原始报错检查邮箱配置和收件地址；若持续失败，请联系邮箱服务商。";

const getRawErrorText = (rawError?: string | null) =>
  rawError && rawError.trim() ? rawError : "服务商未返回原始报错";

export const EmailDeliveryFailureDetails = ({
  possibleCause,
  rawError,
}: EmailDeliveryFailureDetailsProps) => (
  <dl className="mt-2 space-y-1.5 text-xs">
    <div className="grid min-w-0 gap-x-2 gap-y-0.5 sm:grid-cols-[64px_minmax(0,1fr)]">
      <dt className="font-medium text-red-800">可能原因</dt>
      <dd className="min-w-0 leading-5 text-red-700">
        {possibleCause?.trim() || UNKNOWN_CAUSE}
      </dd>
    </div>
    <div className="grid min-w-0 gap-x-2 gap-y-0.5 sm:grid-cols-[64px_minmax(0,1fr)]">
      <dt className="font-medium text-stone-600">原始报错</dt>
      <dd className="min-w-0 overflow-x-auto whitespace-nowrap font-mono text-[11px] leading-5 text-stone-500">
        {getRawErrorText(rawError)}
      </dd>
    </div>
  </dl>
);
