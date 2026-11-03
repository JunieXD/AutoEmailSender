import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { openUrl } from "@tauri-apps/plugin-opener";
import { useEffect } from "react";
import { call, errorMessage } from "@/api/call";
import type { TrackingPack } from "@/api/types";
import { useAccount } from "@/components/account";
import { TRACKING_CAVEAT } from "@/components/tracking";
import { buttonVariants, Button } from "@/components/ui/button";
import { useNow } from "@/now";
import { Section, SwitchButton } from "./settings";

/**
 * Settings › 邮件 › 邮件追踪 (docs/ui.md#交互). The quota lives on the server and packs are bought in
 * the browser, so coming back reads it again.
 */
export function TrackingSection() {
  const account = useAccount();
  const email = account.data?.kind === "loggedIn" ? account.data.email : null;
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["getTracking", email], queryFn: () => call("getTracking", {}) });
  const save = useMutation({
    mutationFn: (enabled: boolean) => call("setTracking", { enabled }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["getTracking"] }),
  });
  const now = useNow();
  const { refetch } = status;
  useEffect(() => {
    const refresh = () => void refetch({ cancelRefetch: false });
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [refetch]);
  const data = status.data;
  const server = data?.account?.kind === "read" ? data.account : null;
  const sponsored = server?.sponsorUntil != null && server.sponsorUntil > now;
  const enabled = data?.enabled ?? false;
  return (
    <Section title="邮件追踪">
      <div>
        <div className="flex items-center justify-between gap-4">
          <span>邮件追踪</span>
          <SwitchButton
            label="邮件追踪"
            checked={enabled}
            disabled={!data || save.isPending || (!enabled && !sponsored)}
            onClick={() => save.mutate(!enabled)}
          />
        </div>
        <div className="mt-2 flex flex-col gap-2 text-sm text-muted-foreground">
          <p>
            开启后，发给导师的首封和追问里会放一张看不见的小图片，导师打开邮件、加载图片时记一次，往来里能看到打开了几次。回复不放。
          </p>
          <p>
            官方服务器能看到每封信的随机编号、打开时间、打开者的 IP
            和设备信息，只保存编号、次数和时间，看不到收件人和邮件内容。
          </p>
          <p>{TRACKING_CAVEAT}</p>
        </div>
        {status.error && <p className="mt-2 text-sm text-destructive">设置未能加载：{errorMessage(status.error)}</p>}
        {save.error && <p className="mt-2 text-sm text-destructive">{errorMessage(save.error)}</p>}
      </div>
      {data && !data.account && <Prompt text="登录并成为赞助者后可以用。" action="去登录" />}
      {data?.account?.kind === "unreadable" && (
        <p className="text-sm text-destructive">额度未能读取：{data.account.reason}</p>
      )}
      {server && !sponsored && (
        <Prompt
          text={
            server.left > 0
              ? `赞助者可以用邮件追踪。还剩 ${server.left} 封，续上赞助后接着用。`
              : "赞助者可以用邮件追踪。"
          }
          action="去赞助"
        />
      )}
      {server && sponsored && <Quota left={server.left} email={email} />}
      {enabled && server && (
        <>
          {!sponsored && (
            <p className="text-sm text-warning">赞助已到期，之后发出的信不带追踪图片，已追踪的照常更新。</p>
          )}
          {sponsored && server.left === 0 && (
            <p className="text-sm text-warning">额度已用完，之后发出的信不带追踪图片。</p>
          )}
        </>
      )}
      {data?.failure && <p className="text-sm text-destructive">同步打开记录失败：{data.failure}之后会自动再试。</p>}
    </Section>
  );
}

function Prompt({ text, action }: { text: string; action: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <p className="text-sm text-muted-foreground">{text}</p>
      <Link to="/settings/account" className={buttonVariants({ variant: "quiet", size: "sm" })}>
        {action}
      </Link>
    </div>
  );
}

function Quota({ left, email }: { left: number; email: string | null }) {
  const tiers = useQuery({ queryKey: ["getSponsorTiers", email], queryFn: () => call("getSponsorTiers", {}) });
  const open = useMutation({ mutationFn: (pack: TrackingPack) => openUrl(pack.url) });
  return (
    <div className="flex flex-col gap-1.5">
      <p className="font-medium">剩余 {left} 封</p>
      {tiers.error ? (
        <p className="text-sm text-destructive">额度包未能读取：{errorMessage(tiers.error)}</p>
      ) : (
        tiers.data && (
          <div className="grid max-w-md grid-cols-3 gap-2">
            {tiers.data.packs.map((pack) => (
              <Button
                key={pack.url}
                variant="quiet"
                className="h-auto flex-col items-start gap-0.5 px-3 py-2.5 text-left font-normal whitespace-normal"
                disabled={open.isPending}
                onClick={() => open.mutate(pack)}
              >
                <span className="text-sm">{pack.count} 封</span>
                <span className="text-lg font-semibold">{pack.price / 100} 元</span>
              </Button>
            ))}
          </div>
        )
      )}
      <p className="text-xs text-muted-foreground">
        一封信用 1 封，打开多少次都不再扣。额度不过期，赞助有效时才能用，赞助到期后续上就能接着用。
      </p>
      {open.error && (
        <p className="text-sm text-destructive">未能打开爱发电：{errorMessage(open.error)}。请检查默认浏览器后再试。</p>
      )}
    </div>
  );
}
