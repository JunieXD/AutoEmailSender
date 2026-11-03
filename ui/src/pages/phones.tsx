import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listen } from "@tauri-apps/api/event";
import { useEffect, useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { PhoneCode, RelayStatus } from "@/api/types";
import { AccountDialog, useAccount } from "@/components/account";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { day, time } from "@/format";
import { Row, SwitchButton } from "./settings";

const MAX_PHONES = 5;

/** Settings › 应用 › 手机和 Agent: using the app from a phone (docs/ui.md). */
export function PhonesRow() {
  const account = useAccount();
  const [login, setLogin] = useState(false);
  const state = account.data;
  return (
    <Row label="手机">
      {account.error && <p className="text-sm text-destructive">账号未能读取：{errorMessage(account.error)}</p>}
      {state?.kind === "loggedIn" && <Phones key={state.email} />}
      {state && state.kind !== "loggedIn" && (
        <>
          <p className="text-sm text-muted-foreground">登录后，可以用手机浏览器远程使用这台电脑上的应用。</p>
          <div className="mt-1.5">
            <Button variant="quiet" onClick={() => setLogin(true)}>
              登录
            </Button>
          </div>
          {login && (
            <AccountDialog
              mode="login"
              email={state.kind === "expired" ? state.email : undefined}
              onClose={() => setLogin(false)}
            />
          )}
        </>
      )}
    </Row>
  );
}

function Phones() {
  const queryClient = useQueryClient();
  const access = useQuery({ queryKey: ["getPhoneAccess"], queryFn: () => call("getPhoneAccess", {}) });
  const enabled = access.data?.enabled ?? false;
  const phones = useQuery({ queryKey: ["listPhones"], queryFn: () => call("listPhones", {}), enabled });
  useEffect(() => {
    const stop = listen("phones-changed", () => {
      void queryClient.invalidateQueries({ queryKey: ["getPhoneAccess"] });
      void queryClient.invalidateQueries({ queryKey: ["listPhones"] });
    });
    return () => void stop.then((f) => f());
  }, [queryClient]);
  const save = useMutation({
    mutationFn: (on: boolean) => call("setPhoneAccess", { enabled: on }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["getPhoneAccess"] }),
  });
  const revoke = useMutation({
    mutationFn: (id: string) => call("revokePhone", { id }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["listPhones"] }),
  });
  const add = useMutation({ mutationFn: () => call("addPhone", {}) });
  // Leaving the page, or the account, leaves the code on screen too.
  useEffect(() => () => void call("cancelPhonePairing", {}), []);
  const full = (phones.data?.length ?? 0) >= MAX_PHONES;
  return (
    <>
      <div>
        <label className="flex items-center justify-between gap-4">
          <span>允许手机连接</span>
          <SwitchButton
            label="允许手机连接"
            checked={enabled}
            disabled={access.data === undefined || save.isPending}
            onClick={() => save.mutate(!enabled)}
          />
        </label>
        <p className="mt-2 text-sm text-muted-foreground">
          手机经官方中继连到这台电脑，内容端到端加密，中继看不到。这台电脑上的应用开着时才能用。
        </p>
        {enabled && access.data && <StatusLine status={access.data.status} />}
        {access.error && <p className="mt-2 text-sm text-destructive">设置未能加载：{errorMessage(access.error)}</p>}
        {save.error && <p className="mt-2 text-sm text-destructive">设置未能保存：{errorMessage(save.error)}</p>}
      </div>
      {enabled && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-col items-start gap-1.5">
            <Button disabled={full || add.isPending} onClick={() => add.mutate()}>
              添加手机
            </Button>
            {full && <p className="text-sm text-muted-foreground">最多配对 {MAX_PHONES} 部手机，先撤销一部</p>}
            {add.error && <p className="text-sm text-destructive">未能生成二维码：{errorMessage(add.error)}</p>}
          </div>
          {phones.error && <p className="text-sm text-destructive">手机未能读取：{errorMessage(phones.error)}</p>}
          {phones.data && phones.data.length > 0 && (
            <ul className="divide-y divide-border rounded-lg border border-border">
              {phones.data.map((phone) => (
                <li key={phone.id} className="flex items-center justify-between gap-4 px-4 py-2.5">
                  <div className="min-w-0">
                    <p>{phone.name}</p>
                    <p className="text-sm text-muted-foreground">
                      配对于 {day(phone.pairedAt)} ·{" "}
                      {phone.online
                        ? "在线"
                        : phone.seenAt === null
                          ? "还没连接过"
                          : `最近连接 ${day(phone.seenAt)} ${time(phone.seenAt)}`}
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    className="shrink-0"
                    disabled={revoke.isPending}
                    onClick={() => revoke.mutate(phone.id)}
                  >
                    撤销
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {revoke.error && <p className="text-sm text-destructive">未能撤销：{errorMessage(revoke.error)}</p>}
        </div>
      )}
      {add.data && (
        <PairingDialog
          // A new code starts over.
          key={add.data.url}
          code={add.data}
          regenerating={add.isPending}
          onRegenerate={() => add.mutate()}
          onClose={() => add.reset()}
        />
      )}
    </>
  );
}

function StatusLine({ status }: { status: RelayStatus }) {
  if (status.kind === "online") return <p className="mt-2 text-sm text-success">手机现在可以连接</p>;
  if (status.kind === "failed") {
    return <p className="mt-2 text-sm text-warning">连不上服务器：{status.reason}（会自动重试）</p>;
  }
  return <p className="mt-2 text-sm text-muted-foreground">正在连接服务器…</p>;
}

/** The code to scan, then the question about the phone that scanned it (docs/ui.md). */
function PairingDialog({
  code,
  regenerating,
  onRegenerate,
  onClose,
}: {
  code: PhoneCode;
  regenerating: boolean;
  onRegenerate: () => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [now, setNow] = useState(Date.now);
  // The phone that scanned it asks under this name; it left when the core says null.
  const [asking, setAsking] = useState<string | null>(null);
  const [left, setLeft] = useState(false);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    const stop = listen<string | null>("phone-pairing", ({ payload }) => {
      setAsking(payload);
      setLeft(payload === null);
    });
    return () => void stop.then((f) => f());
  }, []);
  const answer = useMutation({
    mutationFn: (allow: boolean) => call("answerPhonePairing", { allow }),
    onSuccess: (_, allow) => {
      if (!allow) onClose();
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["listPhones"] }),
  });
  const paired = answer.isSuccess && answer.variables;
  const close = () => {
    if (!paired) void call("cancelPhonePairing", {});
    onClose();
  };
  const seconds = Math.max(0, Math.ceil((code.expiresAt - now) / 1000));
  const expired = left || (asking === null && seconds === 0);
  return (
    <Dialog open onOpenChange={(open) => !open && !answer.isPending && close()}>
      <DialogContent className="w-[420px]">
        <DialogHeader>添加手机</DialogHeader>
        <DialogBody className="flex flex-col items-center gap-3 py-4 text-center text-sm">
          {paired ? (
            <p>已配对</p>
          ) : asking !== null ? (
            <>
              <p className="text-base">允许这台手机连接吗？</p>
              <p className="text-muted-foreground">{asking}</p>
            </>
          ) : expired ? (
            <>
              <div className="grid size-[240px] place-items-center rounded-lg bg-muted text-muted-foreground">
                二维码已失效
              </div>
              <Button variant="quiet" disabled={regenerating} onClick={onRegenerate}>
                重新生成
              </Button>
            </>
          ) : (
            <>
              <img
                className="size-[240px] rounded-lg bg-white p-2"
                alt="配对二维码"
                src={`data:image/svg+xml;utf8,${encodeURIComponent(code.svg)}`}
              />
              <p className="text-muted-foreground">
                用手机的相机或微信扫一扫，{Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")} 内有效。
              </p>
            </>
          )}
          {answer.error && <p className="text-destructive">{errorMessage(answer.error)}</p>}
        </DialogBody>
        {asking !== null && !paired && (
          <DialogFooter>
            <Button variant="quiet" disabled={answer.isPending} onClick={() => answer.mutate(false)}>
              拒绝
            </Button>
            <Button disabled={answer.isPending} onClick={() => answer.mutate(true)}>
              允许
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
