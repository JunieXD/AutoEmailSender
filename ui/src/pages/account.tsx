import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { Tier } from "@/api/types";
import { AccountDialog, type AccountMode, TermsNotice, useAccount } from "@/components/account";
import { Confirm } from "@/components/batch-bar";
import { Field, formClose } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import {
  durationLabel,
  PayDialog,
  perDay,
  REFUNDED,
  usePlus,
  usePlusItems,
  useRefreshPlus,
  yuan,
} from "@/components/plus";
import { Input } from "@/components/ui/input";
import { day, time } from "@/format";
import { LegalLink } from "@/legal";
import { useNow } from "@/now";
import { Row, Section } from "./settings";

/** Settings › 应用 › 账号和 Plus (docs/ui.md#交互). */
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
    <Section title="账号和 Plus">
      {account.error && <p className="text-sm text-destructive">账号未能读取：{errorMessage(account.error)}</p>}
      {state?.kind === "loggedOut" && (
        <>
          <p className="text-sm text-muted-foreground">
            本机的功能无需登录。登录后还可使用导师库和手机远程，开通 Plus 还可使用邮件追踪、云端定时发送和云端备份。
          </p>
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
              <PlusRow key={state.email} email={state.email} outdated={state.termsOutdated} />
              {!state.termsOutdated && <InviteRow key={`invite ${state.email}`} email={state.email} />}
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
                {state.cloudEmails > 0 ? (
                  <Confirm
                    trigger={
                      <Button variant="quiet" disabled={logout.isPending}>
                        退出登录
                      </Button>
                    }
                    message={`有 ${state.cloudEmails} 封邮件已交给云端，退出后照常发出，重新登录这个账号后才能同步结果或撤回。`}
                    action="退出登录"
                    side="bottom"
                    onConfirm={() => logout.mutate()}
                  />
                ) : (
                  <Button variant="quiet" disabled={logout.isPending} onClick={() => logout.mutate()}>
                    退出登录
                  </Button>
                )}
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

/** Paying happens in the browser, so coming back reads Plus again. */
function PlusRow({ email, outdated }: { email: string; outdated: boolean }) {
  const plus = usePlus(email);
  const items = usePlusItems(email, !outdated);
  const now = useNow();
  const refreshPlus = useRefreshPlus(email);
  const [buying, setBuying] = useState<Tier | null>(null);
  const [redeeming, setRedeeming] = useState(false);
  const [redeemed, setRedeemed] = useState<number | null>(null);
  const { refetch } = plus;
  const { refetch: refetchItems } = items;
  useEffect(() => {
    // What each tier would extend Plus to moves with Plus.
    const refresh = () => {
      void refetch({ cancelRefetch: false });
      if (!outdated) void refetchItems({ cancelRefetch: false });
    };
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [refetch, refetchItems, outdated]);
  const until = plus.data?.plusUntil;
  const active = until != null && until > now;
  return (
    <Row label="Plus">
      {plus.error ? (
        <p className="text-sm text-destructive">Plus 状态未能读取：{errorMessage(plus.error)}</p>
      ) : plus.data ? (
        <p className="text-sm">
          {until == null ? "尚未开通 Plus" : active ? `Plus，到${endOf(until)}` : `Plus 已于${day(until)}到期`}
        </p>
      ) : (
        <p className="text-sm text-muted-foreground">正在读取…</p>
      )}
      {redeemed != null && <p className="text-sm text-success">兑换成功，Plus 增加 {redeemed} 天。</p>}
      <p className="text-sm text-muted-foreground">
        本机的功能一直免费。Plus 可使用邮件追踪（每 30 天 500 封）、云端定时发送和云端备份，费用用于支付服务器。
      </p>
      {outdated ? (
        <>
          <TermsNotice />
          <p className="text-sm text-muted-foreground">
            未到期的 Plus 和未用完的额度包，可发邮件到 support@auto-email-sender.cn 申请按剩余部分退款。
          </p>
        </>
      ) : items.error ? (
        <p className="text-sm text-destructive">{errorMessage(items.error)}</p>
      ) : (
        items.data &&
        (items.data.refunded ? (
          <p className="text-sm text-muted-foreground">{REFUNDED}</p>
        ) : (
          <>
            <p className="mt-1 text-sm font-medium">{active ? "续 Plus" : "开通 Plus"}</p>
            <div className="grid max-w-md grid-cols-2 gap-2">
              {items.data.tiers.map((tier) => (
                <Button
                  key={tier.item}
                  variant="quiet"
                  className="h-auto flex-col items-start gap-0.5 px-3 py-2.5 text-left font-normal whitespace-normal"
                  disabled={!tier.available}
                  onClick={() => setBuying(tier)}
                >
                  <span className="text-sm">{durationLabel(tier.duration)}</span>
                  <span className="text-lg font-semibold">{yuan(tier.price)}</span>
                  <span className="text-xs text-muted-foreground">
                    {tier.available ? `约每天 ${perDay(tier).toFixed(2)} 元` : `最多可购买至${day(items.data.latest)}`}
                  </span>
                </Button>
              ))}
            </div>
          </>
        ))
      )}
      {!outdated && (
        <div>
          <Button variant="ghost" onClick={() => setRedeeming(true)}>
            有兑换码？
          </Button>
        </div>
      )}
      {buying && <PayDialog email={email} purchase={{ tier: buying }} onClose={() => setBuying(null)} />}
      {redeeming && (
        <RedeemDialog
          submit={async (code) => {
            const result = await call("redeemCode", { code });
            await refreshPlus();
            setRedeemed(result.days);
          }}
          onClose={() => setRedeeming(false)}
        />
      )}
    </Row>
  );
}

/** "1月28日", or the time as well when it ends today. */
function endOf(until: number): string {
  const date = day(until);
  return date === "今天" ? `今天 ${time(until)}` : date;
}

/** Inviting is per account like Plus; filling in a friend's code is open for a week after registering. */
function InviteRow({ email }: { email: string }) {
  const invite = useQuery({ queryKey: ["getInvite", email], queryFn: () => call("getInvite", {}) });
  const now = useNow();
  const refreshPlus = useRefreshPlus(email);
  const queryClient = useQueryClient();
  const [code, setCode] = useState("");
  const copy = useMutation({ mutationFn: (text: string) => navigator.clipboard.writeText(text) });
  const fill = useMutation({
    mutationFn: () => call("fillInvite", { code }),
    onSuccess: async () => {
      void queryClient.invalidateQueries({ queryKey: ["getInvite", email] });
      await refreshPlus();
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
          用常用邮箱（QQ、163、Gmail 等）或学校邮箱注册的账号方可参加邀请。
        </p>
      ) : (
        <>
          {state.invited < 4 ? (
            <>
              <p className="text-sm text-muted-foreground">
                同学注册后填你的邀请码，你们各得 3 天 Plus。最多邀请 4 位，已邀请 {state.invited} 位。
              </p>
              <div className="flex items-center gap-3">
                <span className="font-mono text-lg tracking-widest select-all">{state.code}</span>
                <Button variant="quiet" size="sm" onClick={() => copy.mutate(state.code)}>
                  {copy.isSuccess ? "已复制" : "复制"}
                </Button>
              </div>
              {copy.error && <p className="text-sm text-destructive">未能复制，请选中邀请码手动复制。</p>}
            </>
          ) : (
            <p className="text-sm text-muted-foreground">已邀请 4 位，谢谢你的推荐。</p>
          )}
          {fill.isSuccess ? (
            <p className="text-sm text-success">
              领取成功{fill.data.plusUntil != null && `，Plus 到${day(fill.data.plusUntil)}`}。
            </p>
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

/** A redeem code from an event or a friend, adding days of Plus. */
function RedeemDialog({ submit, onClose }: { submit: (code: string) => Promise<void>; onClose: () => void }) {
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
          <DialogHeader>兑换 Plus</DialogHeader>
          <DialogBody>
            <fieldset disabled={run.isPending} className="flex min-w-0 flex-col gap-3.5">
              <p className="text-sm">填写社群活动或他人赠送的兑换码，兑换后增加 Plus 天数。</p>
              <Field label="兑换码" id="redeem-code">
                <Input
                  ref={inputRef}
                  id="redeem-code"
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
              {run.isPending ? "正在兑换…" : "兑换"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Asks for the account to be deleted after its cooling-off period, confirmed with its password;
 * nothing on this computer is touched.
 */
function DeleteDialog({ email, onClose }: { email: string; onClose: () => void }) {
  const [password, setPassword] = useState("");
  const passwordRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const now = useNow();
  // What is given up, as last read; the tracking status holds Plus and the packs alike.
  const tracking = useQuery({ queryKey: ["getTracking", email], queryFn: () => call("getTracking", {}) });
  const remove = useMutation({
    mutationFn: () => call("deleteAccount", { email, password }),
    // A session found to have run out turns the section to "expired"; a deletion, to logged out.
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["getAccount"] }),
  });
  const read = tracking.data?.account;
  // A read that failed keeps the last data, which must not stand in for it.
  const server = !tracking.error && read?.kind === "read" ? read : null;
  const kept = [
    server?.plusUntil != null && server.plusUntil > now && `Plus 到${day(server.plusUntil)}`,
    server && server.tracking.packLeft > 0 && `额度包还剩 ${server.tracking.packLeft} 封`,
  ].filter(Boolean);
  return (
    <Dialog open onOpenChange={formClose(password !== "" && !remove.isSuccess, remove.isPending, onClose)}>
      <DialogContent className="w-[480px]" initialFocus={passwordRef}>
        {remove.isSuccess ? (
          <>
            <DialogHeader>已申请注销</DialogHeader>
            <DialogBody>
              <p className="text-sm">
                账号 <span className="break-all">{email}</span> 将于{day(remove.data)}
                删除。在此之前用密码登录，可撤销注销。
              </p>
            </DialogBody>
            <DialogFooter className="justify-end">
              <DialogClose render={<Button />}>完成</DialogClose>
            </DialogFooter>
          </>
        ) : (
          <form
            className="flex min-h-0 flex-1 flex-col"
            onSubmit={(e) => {
              e.preventDefault();
              remove.mutate();
            }}
          >
            <DialogHeader>注销账号</DialogHeader>
            <DialogBody>
              <fieldset disabled={remove.isPending} className="flex min-w-0 flex-col gap-3.5 text-sm">
                <p>
                  注销 <span className="break-all">{email}</span> 后有 15
                  天冷静期：各台电脑都会退出登录，账号不能使用，云端待发的邮件不发出；这期间用密码登录可撤销注销。15
                  天后删除该账号和它在服务器上的全部数据（云端备份、云端待发的邮件、追踪记录和配对的设备），依法依约保留的少量记录及期限见
                  <LegalLink legal="privacy" />
                  。本机的数据不受影响。
                </p>
                {/* What is given up is shown before the password counts. */}
                {tracking.error || read?.kind === "unreadable" ? (
                  <p className="text-destructive">
                    Plus 和额度包未能读取：
                    {read?.kind === "unreadable" ? read.reason : errorMessage(tracking.error)}
                  </p>
                ) : !server ? (
                  <p className="text-muted-foreground">正在读取 Plus 和额度包…</p>
                ) : (
                  kept.length > 0 && (
                    <p>
                      {kept.join("、")}
                      ，注销后一并放弃，冷静期内照常计时。如需退款，请先发邮件到 support@auto-email-sender.cn。
                    </p>
                  )
                )}
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
              <Button type="submit" disabled={remove.isPending || password === "" || !server}>
                {remove.isPending ? "正在注销…" : "注销账号"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
