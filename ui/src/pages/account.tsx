import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { call, errorMessage } from "@/api/call";
import { AccountDialog, type AccountMode, useAccount } from "@/components/account";
import { Field, formClose } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
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
          <p className="text-sm text-muted-foreground">
            登录后可以用导师库、官方模型额度和手机远程；赞助者还有邮件追踪、云端定时发送和云端备份。本机的功能都不用登录。
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

/** Deleting the account on the server, confirmed with its password; nothing here is touched. */
function DeleteDialog({ email, onClose }: { email: string; onClose: () => void }) {
  const [password, setPassword] = useState("");
  const passwordRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => call("deleteAccount", { password }),
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
