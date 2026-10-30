import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { cancelRestore, exportBackup, prepareCloudRestore, prepareRestore, restoreBackup } from "@/api/app";
import { call, errorMessage } from "@/api/call";
import type { CloudBackupCopy, RestoreInfo } from "@/api/types";
import { AccountDialog, useAccount } from "@/components/account";
import { Field, formClose } from "@/components/form";
import { scheduledAt } from "@/components/schedule";
import { Button, buttonVariants } from "@/components/ui/button";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { day, fileSize } from "@/format";
import { useNow } from "@/now";
import { Row, Section, SwitchButton } from "./settings";

/** Settings › 应用 › 备份 (docs/ui.md#交互). */
export function BackupSection() {
  return (
    <Section title="备份">
      <LocalBackup />
      <CloudBackupRow />
    </Section>
  );
}

function LocalBackup() {
  const exported = useMutation({ mutationFn: exportBackup });
  const prepare = useMutation({ mutationFn: prepareRestore });
  return (
    <Row label="本地备份">
      <p className="text-sm text-muted-foreground">
        把全部数据导出成一个文件，换电脑或者数据丢了时用它恢复。备份里没有邮箱授权码、API
        Key、抓取时保存的网页和日志，恢复后要重新填授权码和 API Key。
      </p>
      <div className="mt-1.5 flex flex-col items-start gap-1.5">
        <Button disabled={exported.isPending} onClick={() => exported.mutate()}>
          {exported.isPending ? "正在导出…" : "导出备份"}
        </Button>
        {exported.data && <p className="text-sm text-muted-foreground">已导出</p>}
        {exported.error && <p className="text-sm text-destructive">未能导出：{errorMessage(exported.error)}</p>}
      </div>
      <div className="flex flex-col items-start gap-1.5">
        <Button variant="quiet" disabled={prepare.isPending} onClick={() => prepare.mutate()}>
          {prepare.isPending ? "正在检查备份…" : "从备份恢复"}
        </Button>
        {prepare.error && <p className="text-sm text-destructive">无法恢复：{errorMessage(prepare.error)}</p>}
      </div>
      {prepare.data && <RestoreDialog info={prepare.data} onClose={() => prepare.reset()} />}
    </Row>
  );
}

function CloudBackupRow() {
  const account = useAccount();
  const [login, setLogin] = useState(false);
  const state = account.data;
  return (
    <Row label="云端备份">
      {account.error && <p className="text-sm text-destructive">账号未能读取：{errorMessage(account.error)}</p>}
      {state?.kind === "loggedIn" && <CloudBackup key={state.email} email={state.email} />}
      {state && state.kind !== "loggedIn" && (
        <>
          <p className="text-sm text-muted-foreground">
            登录后，赞助者可以把数据加密备份到云端，换电脑时登录就能恢复。
          </p>
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

/** What the cloud holds is read from the server per account; the schedule and failures are local. */
function CloudBackup({ email }: { email: string }) {
  const queryClient = useQueryClient();
  const now = useNow();
  const local = useQuery({ queryKey: ["getCloudBackup"], queryFn: () => call("getCloudBackup", {}) });
  const server = useQuery({
    // A scheduled backup changes what the cloud holds as well.
    queryKey: ["getCloudBackupServer", email, local.data?.lastBackupAt],
    queryFn: () => call("getCloudBackupServer", {}),
    enabled: local.data !== undefined,
    placeholderData: keepPreviousData,
  });
  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["getCloudBackup"] }),
      queryClient.invalidateQueries({ queryKey: ["getCloudBackupServer", email] }),
    ]);
  const backUp = useMutation({ mutationFn: () => call("backUpToCloud", {}), onSettled: refresh });
  const disable = useMutation({ mutationFn: () => call("disableCloudBackup", {}), onSettled: refresh });
  const restore = useMutation({ mutationFn: prepareCloudRestore });
  const [dialog, setDialog] = useState<"enable" | "replace" | "delete" | null>(null);

  const unreadable = (error: Error) => (
    <div className="flex items-center gap-3">
      <p className="text-sm text-destructive">云端备份未能读取：{errorMessage(error)}</p>
      <Button variant="quiet" size="sm" onClick={() => void refresh()}>
        重试
      </Button>
    </div>
  );
  if (local.error) return unreadable(local.error);
  if (!local.data) return <p className="text-sm text-muted-foreground">正在读取…</p>;
  const status = local.data;
  const autoBackup = (
    <div className="flex items-center justify-between gap-4">
      <span className="text-sm">每天自动备份</span>
      <SwitchButton
        label="每天自动备份"
        checked={status.enabled}
        disabled={disable.isPending}
        onClick={() => (status.enabled ? disable.mutate() : setDialog("enable"))}
      />
    </div>
  );
  // Offline, automatic backups can still be turned off.
  if (server.error) {
    return (
      <>
        {status.enabled && autoBackup}
        {unreadable(server.error)}
        {disable.error && <p className="text-sm text-destructive">未能关闭：{errorMessage(disable.error)}</p>}
      </>
    );
  }
  if (!server.data) return <p className="text-sm text-muted-foreground">正在读取…</p>;
  const { sponsorUntil, deleteAt, backup } = server.data;
  const sponsored = sponsorUntil != null && sponsorUntil > now;
  // Another computer's backup, or one this account's key cannot read: replaced only with consent.
  const other = backup && !(backup.thisDevice && backup.summary) ? backup : null;
  const backingUp = status.enabled && sponsored;
  const replacing = other != null && backingUp && status.replace === other.committedAt;
  const paused = other != null && backingUp && !replacing;
  const running = backUp.isPending || status.running;
  const failure = status.failure
    ? `${scheduledAt(status.failure.since)} 起备份没有成功：${status.failure.message}`
    : backUp.error && errorMessage(backUp.error);
  return (
    <>
      {sponsored ? (
        <>
          {autoBackup}
          <p className="text-sm text-muted-foreground">
            在这台电脑上压缩、加密后上传，数据有变化才上传，每个账号最多 30 MB。
          </p>
        </>
      ) : (
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm text-muted-foreground">
            {backup
              ? `赞助已到期，云端备份已停止。现有的备份还能恢复${deleteAt == null ? "" : `，将于${day(deleteAt)}删除`}。`
              : "云端备份是赞助者专享：每天自动备份，换电脑时登录就能恢复。"}
          </p>
          <Link to="/settings/account" className={buttonVariants({ variant: "quiet", size: "sm" })}>
            {backup ? "续赞助" : "去赞助"}
          </Link>
        </div>
      )}
      {other ? (
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm">
            {copyText(other, "云端现在是", status.needsPassword)}
            {paused ? "，这台电脑不再自动备份。" : replacing ? "，下次备份时换成这台电脑的数据。" : "。"}
          </p>
          {paused && (
            <Button variant="quiet" size="sm" onClick={() => setDialog("replace")}>
              用这台电脑替换
            </Button>
          )}
        </div>
      ) : backup ? (
        <p className="text-sm">
          上次备份：{scheduledAt(status.lastBackupAt ?? backup.committedAt)} · 已用 {fileSize(backup.size)} / 30 MB
        </p>
      ) : (
        backingUp && <p className="text-sm">还没有备份</p>
      )}
      {failure && !paused && <p className="text-sm text-destructive">{failure}</p>}
      {((backingUp && !paused) || backup) && (
        <div className="mt-1.5 flex flex-wrap gap-2">
          {backingUp && !paused && (
            <Button disabled={running} onClick={() => backUp.mutate()}>
              {running ? "正在备份…" : "立即备份"}
            </Button>
          )}
          {backup?.summary && (
            <Button variant="quiet" disabled={restore.isPending} onClick={() => restore.mutate()}>
              {restore.isPending ? "正在下载…" : "从云端恢复"}
            </Button>
          )}
          {backup && (
            <Button variant="ghost" onClick={() => setDialog("delete")}>
              删除云端备份
            </Button>
          )}
        </div>
      )}
      {disable.error && <p className="text-sm text-destructive">未能关闭：{errorMessage(disable.error)}</p>}
      {restore.error && <p className="text-sm text-destructive">无法恢复：{errorMessage(restore.error)}</p>}
      {restore.data && <RestoreDialog info={restore.data} onClose={() => restore.reset()} />}
      {(dialog === "enable" || dialog === "replace") && (
        <EnableDialog
          replacing={dialog === "replace"}
          needsPassword={status.needsPassword}
          other={other}
          onClose={() => setDialog(null)}
          onEnabled={() => {
            setDialog(null);
            void refresh();
            backUp.mutate();
          }}
        />
      )}
      {dialog === "delete" && (
        <DeleteDialog
          onClose={() => setDialog(null)}
          onDeleted={() => {
            setDialog(null);
            void refresh();
          }}
        />
      )}
    </>
  );
}

/** "云端现在是 MacBook Pro 在 10月28日 14:05 的备份（120 位导师、85 封邮件）". */
function copyText(copy: CloudBackupCopy, lead: string, locked: boolean): string {
  const at = scheduledAt(copy.committedAt);
  if (copy.summary) {
    const { deviceName, mentorCount, emailCount } = copy.summary;
    return `${lead}${spaced(deviceName ?? "另一台电脑")}在 ${at} 的备份（${mentorCount} 位导师、${emailCount} 封邮件）`;
  }
  // Without a key yet nothing can be read; with one, the backup was encrypted before a password reset.
  return locked ? `${lead}另一台电脑在 ${at} 的备份` : `${lead}一份 ${at} 用旧密码加密的备份，现在解不开了`;
}

/** A computer's name between Chinese words, spaced where it starts or ends with a letter or digit. */
function spaced(name: string): string {
  return `${latin(name.at(0)) ? " " : ""}${name}${latin(name.at(-1)) ? " " : ""}`;
}

function latin(c: string | undefined): boolean {
  return c !== undefined && /[A-Za-z0-9]/.test(c);
}

/**
 * Turning cloud backup on says what leaves the computer; replacing another computer's backup is
 * agreed to here as well. A login from before cloud backup derives its key from the password first,
 * after which another computer's backup can be read and described.
 */
function EnableDialog({
  replacing,
  needsPassword,
  other,
  onClose,
  onEnabled,
}: {
  /** Already on, taking the cloud over from another computer. */
  replacing: boolean;
  needsPassword: boolean;
  other: CloudBackupCopy | null;
  onClose: () => void;
  onEnabled: () => void;
}) {
  const [password, setPassword] = useState("");
  const [locked, setLocked] = useState(needsPassword);
  const [found, setFound] = useState(needsPassword ? null : other);
  const passwordRef = useRef<HTMLInputElement>(null);
  const enable = useMutation({
    mutationFn: () =>
      call("enableCloudBackup", { password: locked ? password : undefined, replace: found?.committedAt }),
    onSuccess: (result) => {
      setLocked(false);
      if (result.kind === "enabled") onEnabled();
      else setFound(result.backup);
    },
  });
  const action = replacing ? "替换" : found ? "开启并替换" : "开启";
  return (
    <Dialog open onOpenChange={formClose(password !== "", enable.isPending, onClose)}>
      <DialogContent className="w-[480px]" initialFocus={locked ? passwordRef : undefined}>
        <form
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(e) => {
            e.preventDefault();
            enable.mutate();
          }}
        >
          <DialogHeader>{replacing ? "用这台电脑替换云端备份" : "开启云端备份"}</DialogHeader>
          <DialogBody>
            <fieldset disabled={enable.isPending} className="flex min-w-0 flex-col gap-3.5 text-sm">
              {!replacing && (
                <>
                  <p>
                    每天把导师、邮件、模板和材料等数据加密后上传到官方服务器，数据有变化才上传。邮箱授权码、API
                    Key、抓取的网页和日志不上传。
                  </p>
                  <p>
                    备份钥匙由账号密码在这台电脑上算出，服务器读不了备份。用邮箱验证码重置密码后，旧的备份就解不开了。
                  </p>
                </>
              )}
              {locked && (
                <Field label="账号密码" id="cloud-backup-password">
                  <Input
                    ref={passwordRef}
                    id="cloud-backup-password"
                    type="password"
                    autoComplete="current-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                  <p className="mt-1.5 text-muted-foreground">用来算出备份钥匙，密码不会离开这台电脑。</p>
                </Field>
              )}
              {found && (
                <p className="text-warning">
                  {copyText(found, "云端已有", false)}，{replacing ? "替换后" : "开启后"}会换成这台电脑的数据。
                </p>
              )}
            </fieldset>
          </DialogBody>
          <DialogFooter>
            <p className="grow text-sm text-destructive" role="alert">
              {enable.error && errorMessage(enable.error)}
            </p>
            <DialogClose render={<Button variant="ghost" />}>取消</DialogClose>
            <Button type="submit" disabled={enable.isPending || (locked && password === "")}>
              {enable.isPending ? `正在${replacing ? "替换" : "开启"}…` : action}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteDialog({ onClose, onDeleted }: { onClose: () => void; onDeleted: () => void }) {
  const remove = useMutation({ mutationFn: () => call("deleteCloudBackup", {}), onSuccess: onDeleted });
  return (
    <Dialog open onOpenChange={formClose(false, remove.isPending, onClose)}>
      <DialogContent className="w-[420px]">
        <DialogHeader>删除云端备份</DialogHeader>
        <DialogBody>
          <p className="text-sm">删除后云端不再有备份，自动备份也会关闭。这台电脑上的数据不动。</p>
        </DialogBody>
        <DialogFooter>
          <p className="grow text-sm text-destructive" role="alert">
            {remove.error && errorMessage(remove.error)}
          </p>
          <DialogClose render={<Button variant="ghost" />}>取消</DialogClose>
          <Button disabled={remove.isPending} onClick={() => remove.mutate()}>
            {remove.isPending ? "正在删除…" : "删除"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Confirms replacing all the data with the backup checked, which restarts the app. */
function RestoreDialog({ info, onClose }: { info: RestoreInfo; onClose: () => void }) {
  const cancel = useMutation({ mutationFn: cancelRestore, onSuccess: onClose });
  const restore = useMutation({ mutationFn: restoreBackup });
  const busy = cancel.isPending || restore.isPending;
  return (
    <Dialog open onOpenChange={(open) => !open && !busy && cancel.mutate()}>
      <DialogContent>
        <DialogHeader>恢复备份</DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-4 text-sm">
            <p>
              这份备份导出于 {scheduledAt(info.exportedAt)}，有 {info.mentorCount} 位导师、{info.emailCount} 封邮件。
            </p>
            <ul className="list-disc pl-5 leading-relaxed">
              <li>
                会替换这台电脑上现有的全部数据。替换前的数据先备份到
                <span className="break-all">“{info.backupDirectory}”</span>。
              </li>
              <li>恢复后要重新登录账号，重新填写各个邮箱的授权码和模型的 API Key，填好之前不收发信。</li>
              <li>旧电脑上的应用请先从托盘或菜单栏的图标退出（只关窗口它还会在后台发信），免得同一封信发两次。</li>
              <li>恢复完应用会自动重启。</li>
            </ul>
          </div>
        </DialogBody>
        <DialogFooter>
          <p className="grow text-sm text-destructive" role="alert">
            {(cancel.error ?? restore.error) && errorMessage(cancel.error ?? restore.error)}
          </p>
          <Button variant="ghost" disabled={busy} onClick={() => cancel.mutate()}>
            取消
          </Button>
          <Button disabled={busy} onClick={() => restore.mutate()}>
            {restore.isPending ? "正在恢复…" : "恢复并重启"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
