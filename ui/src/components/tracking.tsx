import type { EmailTracking } from "@/api/types";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { day, time } from "@/format";

/** Said wherever opens are explained (docs/design.md#邮件追踪). */
export const TRACKING_CAVEAT =
  "结果只作参考：有的邮箱默认不显示图片，打开了也记不到；有的会自动加载图片（比如苹果“邮件”的“保护邮件活动”），没打开也记成打开；你在自己邮箱的“已发送”里打开这封也会记一次；追踪图片还可能略微增加进垃圾箱的概率。";

/** How a sent letter's tracking stands (docs/ui.md#往来); nothing until the server has it. */
export function TrackingNote({ tracking: t }: { tracking: EmailTracking }) {
  if (t.state === "pending") return null;
  if (t.state !== "tracked") return <p className="text-sm text-muted-foreground">未追踪：{UNTRACKED[t.state]}</p>;
  return (
    <Popover>
      <PopoverTrigger
        openOnHover
        className="text-sm text-muted-foreground underline decoration-dotted underline-offset-4"
      >
        {t.lastOpenAt === null ? "还没有打开记录" : `已打开 ${t.opens} 次，最近 ${at(t.lastOpenAt)}`}
      </PopoverTrigger>
      <PopoverContent side="top" className="w-72 p-3 text-muted-foreground">
        {t.firstOpenAt !== null && <p className="mb-1.5 text-foreground">第一次打开：{at(t.firstOpenAt)}</p>}
        <p>{TRACKING_CAVEAT}</p>
      </PopoverContent>
    </Popover>
  );
}

const UNTRACKED = {
  no_quota: "额度不足",
  no_plus: "Plus 已到期",
  expired: "交出 30 天内一直没能登记到服务器",
} satisfies Record<Exclude<EmailTracking["state"], "pending" | "tracked">, string>;

function at(ms: number): string {
  return `${day(ms)} ${time(ms)}`;
}
