import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { call, errorCode, errorMessage } from "@/api/call";
import { Field, formClose } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { LegalLink, TERMS_CHANGES } from "@/legal";

/** Who is logged in on this computer, as saved here; the server is asked only when a feature needs it. */
export function useAccount() {
  return useQuery({ queryKey: ["getAccount"], queryFn: () => call("getAccount", {}) });
}

export type AccountMode = "login" | "register" | "reset";

const MODES = {
  login: { title: "登录", action: "登录", pending: "正在登录…" },
  register: { title: "注册账号", action: "注册", pending: "正在注册…" },
  reset: { title: "忘记密码", action: "重置密码", pending: "正在重置…" },
} as const;

/** Logging in, registering and resetting the password, switching in one dialog (docs/ui.md#交互). */
export function AccountDialog({
  mode: initialMode,
  email: initialEmail = "",
  onClose,
}: {
  mode: AccountMode;
  email?: string;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<AccountMode>(initialMode);
  const [email, setEmail] = useState(initialEmail);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [reset, setReset] = useState(false);
  // The terms are agreed to before the code is sent; the registration records their version.
  const [agreed, setAgreed] = useState(false);
  const [unagreed, setUnagreed] = useState(false);
  // The message of a login refused because the account waits to be deleted.
  const [deleting, setDeleting] = useState<string | null>(null);
  // The server takes one code a minute per address, for registering and resetting alike.
  const [wait, setWait] = useState(0);
  useEffect(() => {
    if (wait === 0) return undefined;
    const timer = setTimeout(() => setWait(wait - 1), 1000);
    return () => clearTimeout(timer);
  }, [wait]);
  const emailRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();

  const send = useMutation({
    mutationFn: (to: string) =>
      call(mode === "reset" ? "sendPasswordResetCode" : "sendRegistrationCode", { email: to }),
    onSuccess: () => setWait(60),
  });
  const submit = useMutation({
    mutationFn: async (restore: boolean) => {
      if (mode === "login") await call("loginAccount", { email, password, restore });
      else if (mode === "register") await call("registerAccount", { email, code, password });
      else await call("resetAccountPassword", { email, code, password });
    },
    onSuccess: () => {
      if (mode !== "reset") return onClose();
      switchTo("login");
      setReset(true);
    },
    onError: (error) => {
      if (errorCode(error) === "account_deleting") setDeleting(errorMessage(error));
    },
    // A reset also signs this computer out when it was the same account.
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["getAccount"] }),
  });

  /** Keeps the address and the wait for the next code; the rest belongs to the other form. */
  function switchTo(next: AccountMode) {
    setMode(next);
    setCode("");
    setPassword("");
    setReset(false);
    setUnagreed(false);
    send.reset();
    submit.reset();
  }

  const complete =
    email.trim() !== "" &&
    password !== "" &&
    (mode === "login" || code.trim() !== "") &&
    (mode !== "register" || agreed);
  const dirty = email !== initialEmail || code !== "" || password !== "";
  const error = deleting ? null : (submit.error ?? send.error);
  const link = "text-brand-text hover:underline";
  return (
    <Dialog open onOpenChange={formClose(dirty, submit.isPending, onClose)}>
      <DialogContent className="w-[460px]" initialFocus={initialEmail ? passwordRef : emailRef}>
        <form
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(e) => {
            e.preventDefault();
            submit.mutate(false);
          }}
        >
          <DialogHeader>{MODES[mode].title}</DialogHeader>
          <DialogBody>
            <fieldset disabled={submit.isPending} className="flex min-w-0 flex-col gap-3.5">
              {reset && <p className="text-sm text-success">密码已重置，请用新密码登录。</p>}
              <Field label="邮箱" id="account-email">
                <div className="flex gap-2">
                  <Input
                    ref={emailRef}
                    id="account-email"
                    // Not type="email": the browser would reject full-width input the core normalizes.
                    inputMode="email"
                    autoComplete="email"
                    spellCheck={false}
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                  {mode !== "login" && (
                    <Button
                      type="button"
                      variant="quiet"
                      className="w-[118px]"
                      disabled={wait > 0 || send.isPending || email.trim() === ""}
                      onClick={() => {
                        submit.reset();
                        if (mode === "register" && !agreed) return setUnagreed(true);
                        send.mutate(email);
                      }}
                    >
                      {wait > 0 ? `${wait} 秒后重发` : send.isSuccess ? "重新发送" : "发送验证码"}
                    </Button>
                  )}
                </div>
              </Field>
              {mode === "register" && (
                <div>
                  <label className="flex items-center gap-2 text-sm">
                    <Checkbox
                      checked={agreed}
                      onCheckedChange={(checked) => {
                        setAgreed(checked);
                        setUnagreed(false);
                      }}
                    />
                    <span>
                      我已阅读并同意
                      <LegalLink legal="terms" />和<LegalLink legal="privacy" />
                    </span>
                  </label>
                  {unagreed && <p className="mt-1 text-sm text-warning">请先阅读并勾选同意条款，再发送验证码。</p>}
                </div>
              )}
              {send.isSuccess && (
                <p className="-mt-1.5 text-sm text-muted-foreground">验证码已发到 {send.variables}，10 分钟内有效。</p>
              )}
              {mode !== "login" && (
                <Field label="验证码" id="account-code">
                  <Input
                    id="account-code"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={6}
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                  />
                </Field>
              )}
              <Field label={mode === "reset" ? "新密码" : "密码"} id="account-password">
                <Input
                  ref={passwordRef}
                  id="account-password"
                  type="password"
                  autoComplete={mode === "login" ? "current-password" : "new-password"}
                  placeholder={mode === "login" ? undefined : "至少 8 位"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </Field>
              {mode === "register" && (
                <p className="text-sm text-muted-foreground">
                  密码也用来加密云端备份。忘记了可以用邮箱重置，但旧的云端备份就解不开了。
                </p>
              )}
              {mode === "reset" && (
                <p className="text-sm text-muted-foreground">
                  重置后，所有电脑上的应用都要重新登录，旧的云端备份也解不开了。
                </p>
              )}
              <p className="flex gap-4 text-sm">
                {mode === "login" ? (
                  <>
                    <button type="button" className={link} onClick={() => switchTo("register")}>
                      注册账号
                    </button>
                    <button type="button" className={link} onClick={() => switchTo("reset")}>
                      忘记密码
                    </button>
                  </>
                ) : (
                  <button type="button" className={link} onClick={() => switchTo("login")}>
                    {mode === "register" ? "已有账号，去登录" : "返回登录"}
                  </button>
                )}
              </p>
            </fieldset>
          </DialogBody>
          <DialogFooter>
            <p className="grow text-sm text-destructive" role="alert">
              {error && errorMessage(error)}
            </p>
            <DialogClose render={<Button variant="ghost" />}>取消</DialogClose>
            <Button type="submit" disabled={submit.isPending || !complete}>
              {submit.isPending ? MODES[mode].pending : MODES[mode].action}
            </Button>
          </DialogFooter>
        </form>
        {deleting && (
          <Dialog open onOpenChange={() => setDeleting(null)}>
            <DialogContent className="w-[420px]">
              <DialogHeader>账号正在注销</DialogHeader>
              <DialogBody>
                <p className="text-sm">{deleting}</p>
              </DialogBody>
              <DialogFooter className="justify-end">
                <DialogClose render={<Button variant="ghost" />}>取消</DialogClose>
                <Button
                  onClick={() => {
                    setDeleting(null);
                    submit.mutate(true);
                  }}
                >
                  撤销注销并登录
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Whether the logged-in account has yet to agree to this app's terms, which stops the server features. */
export function useTermsOutdated(): boolean {
  const state = useAccount().data;
  return state?.kind === "loggedIn" && state.termsOutdated;
}

/** Asks once a run when the terms changed; declining leaves a notice where each server feature was. */
export function TermsUpdate() {
  const outdated = useTermsOutdated();
  const [declined, setDeclined] = useState(false);
  return outdated && !declined && <TermsDialog onClose={() => setDeclined(true)} />;
}

/** In place of a server feature while the terms wait to be agreed to. */
export function TermsNotice() {
  return (
    <div className="flex items-center justify-between gap-4">
      <p className="text-sm text-warning">尚未同意新版条款，同意后方可使用官方服务。</p>
      <AgreeTermsButton />
    </div>
  );
}

export function AgreeTermsButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="quiet" size="sm" onClick={() => setOpen(true)}>
        查看并同意
      </Button>
      {open && <TermsDialog onClose={() => setOpen(false)} />}
    </>
  );
}

/** Answered only with its buttons: agreeing turns the server features back on. */
function TermsDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const agree = useMutation({
    mutationFn: () => call("acceptTerms", {}),
    // Every server feature read so far failed for the terms.
    onSuccess: () => queryClient.invalidateQueries(),
  });
  return (
    <Dialog open>
      <DialogContent className="w-[460px]">
        <DialogHeader closable={false}>用户服务协议和隐私政策已更新</DialogHeader>
        <DialogBody className="flex flex-col gap-2 text-sm">
          <p>{TERMS_CHANGES}</p>
          <p>
            查看全文：
            <LegalLink legal="terms" />
            <LegalLink legal="privacy" />
          </p>
          <p className="text-muted-foreground">
            不同意时，导师库、邮件追踪、云端备份和手机远程这些用到服务器的功能暂停使用，本机的功能不受影响。
          </p>
        </DialogBody>
        <DialogFooter>
          <p className="grow text-sm text-destructive" role="alert">
            {agree.error && errorMessage(agree.error)}
          </p>
          <Button variant="ghost" disabled={agree.isPending} onClick={onClose}>
            不同意
          </Button>
          <Button disabled={agree.isPending} onClick={() => agree.mutate()}>
            {agree.isPending ? "正在提交…" : "同意"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
