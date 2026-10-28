import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { openUrl } from "@tauri-apps/plugin-opener";
import { useEffect, useRef, useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { Duration, Sponsor, SponsorTier } from "@/api/types";
import { AccountDialog, type AccountMode, useAccount } from "@/components/account";
import { Field, formClose } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { day, time } from "@/format";
import { useNow } from "@/now";
import { Row, Section } from "./settings";

/** Settings › 应用 › 账号和赞助 (docs/ui.md#交互). */
export function AccountSection() {
  const account = useAccount();
  const [dialog, setDialog] = useState<AccountMode | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const logout = useMutation({
    mutationFn: () => call("logoutAccount", {}),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["getAccount"] }),
  });
  const state = account.data;
  return (
    <Section title="账号和赞助">
      {account.error && <p className="text-sm text-destructive">账号未能读取：{errorMessage(account.error)}</p>}
      {state?.kind === "loggedOut" && (
        <>
          <p className="text-sm text-muted-foreground">本机的功能都不用登录。登录后还能用导师库。</p>
          <div className="flex gap-2">
            <Button onClick={() => setDialog("login")}>登录</Button>
            <Button variant="quiet" onClick={() => setDialog("register")}>
              注册
            </Button>
          </div>
        </>
      )}
      {state && state.kind !== "loggedOut" && (
        <>
          <Row label="账号">
            <p className="text-sm break-all text-muted-foreground">{state.email}</p>
          </Row>
          {state.kind === "loggedIn" && (
            <>
              <SponsorRow key={state.email} email={state.email} />
              <InviteRow key={`invite ${state.email}`} email={state.email} />
            </>
          )}
          {state.kind === "expired" ? (
            <div className="flex items-center gap-4">
              <p className="text-sm text-warning">登录已失效，请重新登录。</p>
              <Button onClick={() => setDialog("login")}>登录</Button>
            </div>
          ) : (
            <div className="flex flex-col items-start gap-1.5">
              <div className="flex gap-2">
                <Button variant="quiet" disabled={logout.isPending} onClick={() => logout.mutate()}>
                  退出登录
                </Button>
                <Button variant="ghost" onClick={() => setDeleting(state.email)}>
                  注销账号
                </Button>
              </div>
              {logout.error && <p className="text-sm text-destructive">未能退出登录：{errorMessage(logout.error)}</p>}
            </div>
          )}
        </>
      )}
      {deleting && <DeleteDialog email={deleting} onClose={() => setDeleting(null)} />}
      {dialog && (
        <AccountDialog
          mode={dialog}
          email={state?.kind === "expired" ? state.email : undefined}
          onClose={() => setDialog(null)}
        />
      )}
    </Section>
  );
}

/**
 * Sponsorship lives only on the server; paying happens in the browser, so coming back re-reads it.
 * It is kept per account, so another login never shows this one's period.
 * Only the perks already built are named here; docs/ui.md has the full list.
 */
function SponsorRow({ email }: { email: string }) {
  const sponsor = useQuery({ queryKey: ["getSponsor", email], queryFn: () => call("getSponsor", {}) });
  const tiers = useQuery({ queryKey: ["getSponsorTiers", email], queryFn: () => call("getSponsorTiers", {}) });
  const now = useNow();
  const showSponsor = useShowSponsor(email);
  const [dialog, setDialog] = useState<"claim" | "redeem" | null>(null);
  const [redeemed, setRedeemed] = useState<number | null>(null);
  const open = useMutation({ mutationFn: (tier: SponsorTier) => openUrl(tier.url) });
  const { refetch } = sponsor;
  useEffect(() => {
    const refresh = () => void refetch({ cancelRefetch: false });
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [refetch]);
  const until = sponsor.data?.sponsorUntil;
  const active = until != null && until > now;
  const cheapest =
    tiers.data && tiers.data.length > 1
      ? tiers.data.reduce((best, tier) => (perDay(tier) < perDay(best) ? tier : best))
      : undefined;
  return (
    <Row label="赞助">
      {sponsor.error ? (
        <p className="text-sm text-destructive">赞助状态未能读取：{errorMessage(sponsor.error)}</p>
      ) : sponsor.data ? (
        <p className="text-sm">
          {until == null ? "还不是赞助者" : active ? `赞助者，到${endOf(until)}` : `赞助已于${day(until)}到期`}
        </p>
      ) : (
        <p className="text-sm text-muted-foreground">正在读取…</p>
      )}
      {redeemed != null && <p className="text-sm text-success">兑换成功，加了 {redeemed} 天赞助。</p>}
      <p className="text-sm text-muted-foreground">
        本机的功能一直免费。赞助用来支付服务器和导师库的整理，赞助期内导师库不限数量。
      </p>
      {tiers.error ? (
        <p className="text-sm text-destructive">{errorMessage(tiers.error)}</p>
      ) : (
        tiers.data && (
          <>
            <p className="mt-1 text-sm font-medium">{active ? "续赞助" : "选一种时长"}</p>
            <div className="grid max-w-md grid-cols-3 gap-2">
              {tiers.data.map((tier) => (
                <Button
                  key={tier.url}
                  variant="quiet"
                  className="relative h-auto flex-col items-start gap-0.5 px-3 py-2.5 text-left font-normal whitespace-normal"
                  disabled={open.isPending}
                  onClick={() => open.mutate(tier)}
                >
                  {tier === cheapest && (
                    <span className="absolute -top-2 right-2 rounded-full bg-selected px-1.5 text-xs text-brand-text">
                      最划算
                    </span>
                  )}
                  <span className="text-sm">{durationLabel(tier.duration)}</span>
                  <span className="text-lg font-semibold">{tier.price / 100} 元</span>
                  <span className="text-xs text-muted-foreground">约每天 {perDay(tier).toFixed(2)} 元</span>
                </Button>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">
              不自动续费，到期不会扣款。链接带着你的账号编号，用来付款后自动开通。
            </p>
          </>
        )
      )}
      {open.error && <p className="text-sm text-destructive">未能打开爱发电：{errorMessage(open.error)}</p>}
      <div className="flex flex-wrap gap-2">
        <Button variant="ghost" onClick={() => setDialog("claim")}>
          付款后没开通？
        </Button>
        <Button variant="ghost" onClick={() => setDialog("redeem")}>
          有兑换码？
        </Button>
      </div>
      {dialog === "claim" && (
        <CodeDialog
          title="用订单号开通"
          intro="没从应用的链接付款，或付款几分钟后还没开通时，填爱发电“我的订单”里这笔订单的订单号。"
          label="订单号"
          action="开通"
          submit={async (orderNumber) => showSponsor(await call("claimSponsorOrder", { orderNumber }))}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "redeem" && (
        <CodeDialog
          title="兑换赞助"
          intro="填社群活动或别人送你的兑换码，兑换后得到赞助天数。"
          label="兑换码"
          action="兑换"
          submit={async (code) => {
            const result = await call("redeemCode", { code });
            await showSponsor({ sponsorUntil: result.sponsorUntil });
            setRedeemed(result.days);
          }}
          onClose={() => setDialog(null)}
        />
      )}
    </Row>
  );
}

/** Shows a period the server just returned, and the library quota that follows it. */
function useShowSponsor(email: string) {
  const queryClient = useQueryClient();
  return async (sponsor: Sponsor) => {
    // A read already on its way may carry the period from before.
    await queryClient.cancelQueries({ queryKey: ["getSponsor", email] });
    queryClient.setQueryData(["getSponsor", email], sponsor);
    void queryClient.invalidateQueries({ queryKey: ["getLibraryQuota"] });
  };
}

function durationLabel(duration: Duration): string {
  if ("days" in duration) return duration.days === 7 ? "一周" : `${duration.days} 天`;
  if (duration.months === 1) return "一个月";
  return duration.months === 3 ? "三个月" : `${duration.months} 个月`;
}

/** Yuan per day, counting a month as its average length. */
function perDay(tier: SponsorTier): number {
  const days = "days" in tier.duration ? tier.duration.days : tier.duration.months * 30.44;
  return tier.price / 100 / days;
}

/** "1月28日", or the time as well when it ends today. */
function endOf(until: number): string {
  const date = day(until);
  return date === "今天" ? `今天 ${time(until)}` : date;
}

/** Inviting is per account like sponsorship; filling in a friend's code is open for a week after registering. */
function InviteRow({ email }: { email: string }) {
  const invite = useQuery({ queryKey: ["getInvite", email], queryFn: () => call("getInvite", {}) });
  const now = useNow();
  const showSponsor = useShowSponsor(email);
  const queryClient = useQueryClient();
  const [code, setCode] = useState("");
  const copy = useMutation({ mutationFn: (text: string) => navigator.clipboard.writeText(text) });
  const fill = useMutation({
    mutationFn: () => call("fillInvite", { code }),
    onSuccess: async (sponsor) => {
      await showSponsor(sponsor);
      void queryClient.invalidateQueries({ queryKey: ["getInvite", email] });
    },
  });
  const state = invite.data;
  return (
    <Row label="邀请同学">
      {invite.error ? (
        <p className="text-sm text-destructive">邀请未能读取：{errorMessage(invite.error)}</p>
      ) : !state ? (
        <p className="text-sm text-muted-foreground">正在读取…</p>
      ) : state.status === "ineligible" ? (
        <p className="text-sm text-muted-foreground">
          用常用邮箱（QQ、163、Gmail 等）或学校邮箱注册的账号才能参加邀请。
        </p>
      ) : (
        <>
          {state.invited < 4 ? (
            <>
              <p className="text-sm text-muted-foreground">
                同学注册后填你的邀请码，你们各得 3 天赞助者。最多邀请 4 位，已邀请 {state.invited} 位。
              </p>
              <div className="flex items-center gap-3">
                <span className="font-mono text-lg tracking-widest select-all">{state.code}</span>
                <Button variant="quiet" size="sm" onClick={() => copy.mutate(state.code)}>
                  {copy.isSuccess ? "已复制" : "复制"}
                </Button>
              </div>
              {copy.error && <p className="text-sm text-destructive">未能复制：{errorMessage(copy.error)}</p>}
            </>
          ) : (
            <p className="text-sm text-muted-foreground">已邀请 4 位，谢谢你的推荐。</p>
          )}
          {fill.isSuccess ? (
            <p className="text-sm text-success">领取成功，你们各得 3 天赞助者。</p>
          ) : (
            state.fillUntil != null &&
            state.fillUntil > now && (
              <form
                className="mt-1 flex flex-col gap-1.5"
                onSubmit={(e) => {
                  e.preventDefault();
                  fill.mutate();
                }}
              >
                <label htmlFor="invite-code" className="text-sm">
                  有同学的邀请码？
                </label>
                <div className="flex gap-2">
                  <Input
                    id="invite-code"
                    className="w-40"
                    autoComplete="off"
                    disabled={fill.isPending}
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                  />
                  <Button type="submit" variant="quiet" disabled={fill.isPending || code.trim() === ""}>
                    {fill.isPending ? "正在领取…" : "领取"}
                  </Button>
                </div>
                {fill.error && <p className="text-sm text-destructive">{errorMessage(fill.error)}</p>}
              </form>
            )
          )}
        </>
      )}
    </Row>
  );
}

/** One code typed in to extend sponsorship: an 爱发电 order number or a redeem code. */
function CodeDialog({
  title,
  intro,
  label,
  action,
  submit,
  onClose,
}: {
  title: string;
  intro: string;
  label: string;
  action: string;
  submit: (code: string) => Promise<void>;
  onClose: () => void;
}) {
  const [code, setCode] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const run = useMutation({ mutationFn: () => submit(code), onSuccess: onClose });
  return (
    <Dialog open onOpenChange={formClose(code !== "", run.isPending, onClose)}>
      <DialogContent className="w-[460px]" initialFocus={inputRef}>
        <form
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(e) => {
            e.preventDefault();
            run.mutate();
          }}
        >
          <DialogHeader>{title}</DialogHeader>
          <DialogBody>
            <fieldset disabled={run.isPending} className="flex min-w-0 flex-col gap-3.5">
              <p className="text-sm">{intro}</p>
              <Field label={label} id="sponsor-code">
                <Input
                  ref={inputRef}
                  id="sponsor-code"
                  autoComplete="off"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                />
              </Field>
            </fieldset>
          </DialogBody>
          <DialogFooter>
            <p className="grow text-sm text-destructive" role="alert">
              {run.error && errorMessage(run.error)}
            </p>
            <DialogClose render={<Button variant="ghost" />}>取消</DialogClose>
            <Button type="submit" disabled={run.isPending || code.trim() === ""}>
              {run.isPending ? `正在${action}…` : action}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Deleting the account on the server, confirmed with its password; nothing here is touched. */
function DeleteDialog({ email, onClose }: { email: string; onClose: () => void }) {
  const [password, setPassword] = useState("");
  const passwordRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => call("deleteAccount", { email, password }),
    onSuccess: onClose,
    // A session found to have run out turns the section to "expired".
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["getAccount"] }),
  });
  return (
    <Dialog open onOpenChange={formClose(password !== "", remove.isPending, onClose)}>
      <DialogContent className="w-[460px]" initialFocus={passwordRef}>
        <form
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(e) => {
            e.preventDefault();
            remove.mutate();
          }}
        >
          <DialogHeader>注销账号</DialogHeader>
          <DialogBody>
            <fieldset disabled={remove.isPending} className="flex min-w-0 flex-col gap-3.5">
              <p className="text-sm">
                会删除账号 <span className="break-all">{email}</span>
                和它在服务器上的全部数据（云端备份、云端待发的邮件、追踪记录和配对的设备），不能恢复。这台电脑上的数据不动。
              </p>
              <Field label="密码" id="account-delete-password">
                <Input
                  ref={passwordRef}
                  id="account-delete-password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </Field>
            </fieldset>
          </DialogBody>
          <DialogFooter>
            <p className="grow text-sm text-destructive" role="alert">
              {remove.error && errorMessage(remove.error)}
            </p>
            <DialogClose render={<Button variant="ghost" />}>取消</DialogClose>
            <Button type="submit" disabled={remove.isPending || password === ""}>
              {remove.isPending ? "正在注销…" : "注销账号"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
