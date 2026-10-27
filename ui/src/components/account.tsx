import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { call, errorMessage } from "@/api/call";
import { Field, formClose } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

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
    mutationFn: async () => {
      if (mode === "login") await call("loginAccount", { email, password });
      else if (mode === "register") await call("registerAccount", { email, code, password });
      else await call("resetAccountPassword", { email, code, password });
    },
    onSuccess: () => {
      if (mode !== "reset") return onClose();
      switchTo("login");
      setReset(true);
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
    send.reset();
    submit.reset();
  }

  const complete = email.trim() !== "" && password !== "" && (mode === "login" || code.trim() !== "");
  const dirty = email !== initialEmail || code !== "" || password !== "";
  const error = submit.error ?? send.error;
  const link = "text-brand-text hover:underline";
  return (
    <Dialog open onOpenChange={formClose(dirty, submit.isPending, onClose)}>
      <DialogContent className="w-[460px]" initialFocus={initialEmail ? passwordRef : emailRef}>
        <form
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(e) => {
            e.preventDefault();
            submit.mutate();
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
                        send.mutate(email);
                      }}
                    >
                      {wait > 0 ? `${wait} 秒后重发` : send.isSuccess ? "重新发送" : "发送验证码"}
                    </Button>
                  )}
                </div>
              </Field>
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
                <p className="text-sm text-muted-foreground">重置后，所有电脑上的应用都要重新登录。</p>
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
      </DialogContent>
    </Dialog>
  );
}
