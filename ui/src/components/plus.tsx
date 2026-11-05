import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { openUrl } from "@tauri-apps/plugin-opener";
import { useEffect, useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { Method, Pack, Tier } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { day } from "@/format";
import { LegalLink } from "@/legal";

// Plus and tracking packs are paid for in the browser through the payment service (docs/design.md#Plus).

export const REFUNDED = "该账号已办理过无理由退款，不能再购买 Plus 和额度包。依法应退的退款不在此列。";

/** The account's Plus, kept per account so that another login never shows this one's. */
export function usePlus(email: string) {
  return useQuery({ queryKey: ["getPlus", email], queryFn: () => call("getPlus", {}) });
}

export function usePlusItems(email: string, enabled = true) {
  return useQuery({ queryKey: ["getPlusItems", email], queryFn: () => call("getPlusItems", {}), enabled });
}

/** Reads Plus and the tracking quota again after they changed on the server. */
export function useRefreshPlus(email: string) {
  const queryClient = useQueryClient();
  return async () => {
    void queryClient.invalidateQueries({ queryKey: ["getTracking"] });
    void queryClient.invalidateQueries({ queryKey: ["getCloudBackupServer"] });
    void queryClient.invalidateQueries({ queryKey: ["getPlusItems", email] });
    // A read on its way started before the change; invalidating alone would reuse it on the first
    // load or once the page is left, so drop it and read again now or when the page comes back.
    await queryClient.cancelQueries({ queryKey: ["getPlus", email] });
    await queryClient.invalidateQueries({ queryKey: ["getPlus", email] });
  };
}

export function durationLabel(duration: Tier["duration"]): string {
  if ("days" in duration) return `${duration.days} 天`;
  if (duration.months === 1) return "一个月";
  return duration.months === 3 ? "三个月" : `${duration.months} 个月`;
}

/** Yuan per day, counting a month as its average length. */
export function perDay(tier: Tier): number {
  const days = "days" in tier.duration ? tier.duration.days : tier.duration.months * 30.44;
  return tier.price / 100 / days;
}

export function yuan(fen: number): string {
  return `${fen / 100} 元`;
}

export type Purchase = { tier: Tier } | { pack: Pack; plusUntil: number };

const NOTICE =
  "不自动续费，到期不会扣款。开通后 7 天内可申请无理由全额退款：首次开通 Plus 的无条件退款；其他 Plus 订单开通后带追踪的邮件超过 30 封、额度包已使用的，不予退款；办理过无理由退款的账号不能再购买。退款和其他问题请发邮件到 support@auto-email-sender.cn，也可加入 QQ 群 952383261 交流。";

/** How long to wait on the payment page before saying it has not arrived yet. */
const WAIT = 15 * 60_000;

/** Agreeing to the Plus terms and paying in the browser; the order is applied once the payment arrives. */
export function PayDialog({ email, purchase, onClose }: { email: string; purchase: Purchase; onClose: () => void }) {
  const [agreed, setAgreed] = useState(false);
  const order = useMutation({
    mutationFn: async (method: Method) => {
      const created = await call("createPlusOrder", {
        item: ("tier" in purchase ? purchase.tier : purchase.pack).item,
        method,
      });
      await openUrl(created.payUrl);
      return created.order;
    },
  });
  const what =
    "tier" in purchase
      ? `Plus ${durationLabel(purchase.tier.duration)}，${yuan(purchase.tier.price)}。开通后 Plus 到${day(purchase.tier.until)}。`
      : `额度包 ${purchase.pack.count} 封，${yuan(purchase.pack.price)}。额度包${day(purchase.pack.expiresAt)}到期，Plus ${day(purchase.plusUntil)}到期；Plus 到期后额度包暂停使用，有效期照常计算。`;
  return (
    // The page opens once the order is placed, so the dialog stays until then.
    <Dialog open onOpenChange={(open) => !open && !order.isPending && onClose()}>
      <DialogContent className="w-[480px]">
        <DialogHeader>{"tier" in purchase ? "开通 Plus" : "购买额度包"}</DialogHeader>
        {order.data === undefined ? (
          <>
            <DialogBody className="flex flex-col gap-3 text-sm">
              <p className="font-medium">{what}</p>
              <p className="text-muted-foreground">{NOTICE}</p>
              <label className="flex items-center gap-2">
                <Checkbox checked={agreed} disabled={order.isPending} onCheckedChange={setAgreed} />
                <span>
                  我已阅读并同意
                  <LegalLink legal="plus" />
                </span>
              </label>
            </DialogBody>
            <DialogFooter>
              <p className="grow text-sm text-destructive" role="alert">
                {order.error && errorMessage(order.error)}
              </p>
              <Button disabled={!agreed || order.isPending} onClick={() => order.mutate("alipay")}>
                支付宝
              </Button>
              <Button disabled={!agreed || order.isPending} onClick={() => order.mutate("wxpay")}>
                微信支付
              </Button>
            </DialogFooter>
          </>
        ) : (
          <Paying email={email} order={order.data} pack={!("tier" in purchase)} />
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Asks the server after the order until it is applied, for as long as paying takes. */
function Paying({ email, order, pack }: { email: string; order: number; pack: boolean }) {
  const refresh = useRefreshPlus(email);
  const [waited, setWaited] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setWaited(true), WAIT);
    return () => clearTimeout(timer);
  }, []);
  const state = useQuery({
    queryKey: ["getPlusOrder", order],
    queryFn: async () => {
      const answer = await call("getPlusOrder", { order });
      if (answer.state === "applied") await refresh();
      return answer;
    },
    // The user pays in the browser, with the app in the background.
    refetchInterval: (query) => (waited || query.state.data?.state === "applied" ? false : 3000),
    refetchIntervalInBackground: true,
  });
  const applied = state.data?.state === "applied" ? state.data : null;
  return (
    <>
      <DialogBody className="flex flex-col gap-3 text-sm">
        {applied ? (
          <p className="text-success">
            {pack
              ? `已开通，额度包剩 ${applied.tracking.packLeft} 封。`
              : `已开通${applied.plusUntil == null ? "" : `，Plus 到${day(applied.plusUntil)}`}。`}
          </p>
        ) : (
          <>
            <p>{waited ? "尚未收到付款。如已付款，稍后会自动开通。" : "请在浏览器中完成付款，付款后自动开通。"}</p>
            {state.error && <p className="text-muted-foreground">订单状态暂时未能读取：{errorMessage(state.error)}</p>}
          </>
        )}
      </DialogBody>
      <DialogFooter className="justify-end">
        {applied ? (
          <DialogClose render={<Button />}>完成</DialogClose>
        ) : (
          <DialogClose render={<Button variant="ghost" />}>{waited ? "关闭" : "取消"}</DialogClose>
        )}
      </DialogFooter>
    </>
  );
}
