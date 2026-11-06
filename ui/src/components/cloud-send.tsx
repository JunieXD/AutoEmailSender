import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { call, errorMessage } from "@/api/call";
import type {
  CloudHandedOff,
  CloudSendBlocked,
  CloudSendPlanned,
  CloudSendPreview,
  CloudSendUnfitReason,
  CloudWithdraw,
} from "@/api/types";
import { refreshSchedule, scheduledAt } from "@/components/schedule";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { fileSize } from "@/format";

// docs/ui.md#任务: handing scheduled first emails to the official server, which sends them while the
// computer is off.

const BLOCKED: Record<CloudSendBlocked, string> = {
  signed_out: "云端定时发送是 Plus 专享，请先登录。",
  terms_outdated: "尚未同意新版条款，同意后方可使用官方服务。",
  no_plus: "云端定时发送是 Plus 专享。",
};

const REASONS: Record<CloudSendUnfitReason, string> = {
  follow_up: "追问",
  tried: "之前发送过一次",
  task_paused: "任务已暂停",
  no_address: "导师没有邮箱",
  too_soon: "时间太近",
  after_plus: "晚于 Plus 到期时间",
  foreign_mailbox: "境外邮箱",
  no_mailbox: "没有能从云端发信的邮箱",
  too_late: "排不进最晚发送时间",
  too_large: "超过上限",
};

/** "交给云端发送" above the emails about to go, or of one task. */
export function CloudSendButton({ task }: { task?: number }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="quiet" size="sm" onClick={() => setOpen(true)}>
        交给云端发送
      </Button>
      {open && <CloudSendDialog task={task} onClose={() => setOpen(false)} />}
    </>
  );
}

export function CloudSendDialog({ task, onClose }: { task?: number; onClose: () => void }) {
  const client = useQueryClient();
  const all = useQuery({
    queryKey: ["cloudSendPreview", task ?? null],
    queryFn: () => call("cloudSendPreview", task === undefined ? {} : { task }),
    staleTime: Infinity,
  });
  // Unticked: the others are planned again without them.
  const [unticked, setUnticked] = useState<number[]>([]);
  const ticked = all.data?.emails.map((e) => e.id).filter((id) => !unticked.includes(id)) ?? [];
  const some = useQuery({
    queryKey: ["cloudSendPreview", "ticked", ticked],
    queryFn: () => call("cloudSendPreview", { emails: ticked }),
    enabled: unticked.length > 0 && ticked.length > 0,
    staleTime: Infinity,
  });
  const plan = unticked.length === 0 ? all.data : some.data;
  const [consents, setConsents] = useState<number[]>([]);
  const handOff = useMutation({
    mutationFn: (chosen: CloudSendPreview) =>
      call("handOffToCloud", {
        emails: chosen.emails.map(({ id, mailboxId, at }) => ({ id, mailboxId, at })),
        consents,
      }),
    onSettled: () => {
      refreshSchedule(client);
      void client.invalidateQueries({ queryKey: ["listMailboxes"] });
    },
  });
  const missing = plan?.mailboxes.filter((m) => m.needsConsent && !consents.includes(m.id)) ?? [];
  const busy = handOff.isPending || (unticked.length > 0 && ticked.length > 0 && some.isFetching);
  return (
    <Dialog open onOpenChange={(open) => !open && !handOff.isPending && onClose()}>
      <DialogContent>
        <DialogHeader>交给云端发送</DialogHeader>
        {handOff.data ? (
          <HandedOff result={handOff.data} />
        ) : (
          <DialogBody className="flex flex-col gap-4 text-sm">
            {all.error && <p className="text-destructive">{errorMessage(all.error)}</p>}
            {all.data && <Choices preview={all.data} plan={plan} unticked={unticked} onUntick={setUnticked} />}
            {plan && plan.emails.length > 0 && (
              <>
                <p className="text-muted-foreground">
                  将上传到官方服务器（上海）：这些邮件的收件人、主题、正文和附件（共 {fileSize(plan.size)}
                  ），以及下列邮箱的地址、服务器设置和授权码。邮件和授权码加密保存，但服务器发信时需要解开，不是端到端加密；发出、失败或撤回后即删除。交出后这台电脑不再发送这些邮件，到时间前可以撤回。
                </p>
                <ul className="flex flex-col gap-2">
                  {plan.mailboxes.map((m) => (
                    <li key={m.id}>
                      {m.needsConsent ? (
                        <label className="flex items-center gap-2">
                          <Checkbox
                            checked={consents.includes(m.id)}
                            disabled={handOff.isPending}
                            onCheckedChange={(on) =>
                              setConsents((c) => (on ? [...c, m.id] : c.filter((id) => id !== m.id)))
                            }
                          />
                          <span>同意把 {m.address} 的授权码交给官方服务器，用来按时发信</span>
                        </label>
                      ) : (
                        <span>{m.address}（云端已有授权码）</span>
                      )}
                    </li>
                  ))}
                </ul>
              </>
            )}
            {some.error && <p className="text-destructive">{errorMessage(some.error)}</p>}
          </DialogBody>
        )}
        <DialogFooter>
          <p className="grow text-sm text-destructive" role="alert">
            {handOff.error && errorMessage(handOff.error)}
            {!handOff.error && !handOff.data && missing.length > 0 && (
              <span className="text-muted-foreground">勾选同意后才能交出；不想交出的，取消勾选用这个邮箱的邮件。</span>
            )}
          </p>
          {handOff.data ? (
            <DialogClose render={<Button />}>完成</DialogClose>
          ) : (
            plan &&
            plan.emails.length > 0 && (
              <Button disabled={busy || missing.length > 0} onClick={() => handOff.mutate(plan)}>
                {handOff.isPending ? "正在上传…" : "交给云端"}
              </Button>
            )
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** The emails that can go, by task and ticked by default, and why the others cannot. */
function Choices({
  preview,
  plan,
  unticked,
  onUntick,
}: {
  preview: CloudSendPreview;
  /** As planned with the ticks; undefined while it is planned again. */
  plan: CloudSendPreview | undefined;
  unticked: number[];
  onUntick: (ids: number[]) => void;
}) {
  if (preview.blocked !== null)
    return (
      <p>
        {BLOCKED[preview.blocked]}
        <Link to="/settings/account" className="ml-2 text-brand-text">
          账号和 Plus
        </Link>
      </p>
    );
  const groups = new Map<string, CloudSendPlanned[]>();
  for (const e of preview.emails) {
    const name = e.taskName ?? "单独定时的首封";
    groups.set(name, [...(groups.get(name) ?? []), e]);
  }
  const reasons = new Map<CloudSendUnfitReason, number>();
  for (const u of preview.unfit) reasons.set(u.reason, (reasons.get(u.reason) ?? 0) + 1);
  const mailboxes = new Map((plan ?? preview).mailboxes.map((m) => [m.id, m.address]));
  return (
    <>
      {preview.emails.length === 0 && (
        <p>
          {preview.unfit.length === 0
            ? "只有定时发送的首封可以交给云端，现在没有还没发出的定时首封。"
            : "没有能交给云端的邮件。"}
        </p>
      )}
      {[...groups].map(([name, emails]) => (
        <section key={name} className="flex flex-col gap-1.5">
          <h3 className="font-medium">{name}</h3>
          {emails.map((e) => {
            const planned = plan?.emails.find((p) => p.id === e.id);
            const ticked = !unticked.includes(e.id);
            return (
              <label key={e.id} className="flex items-center gap-2">
                <Checkbox
                  checked={ticked}
                  onCheckedChange={(on) => onUntick(on ? unticked.filter((id) => id !== e.id) : [...unticked, e.id])}
                />
                <span className="min-w-0 truncate">{e.mentorName}</span>
                <span className="ml-auto shrink-0 text-muted-foreground tabular-nums">
                  {ticked && planned
                    ? `${scheduledAt(planned.at)} 从 ${mailboxes.get(planned.mailboxId) ?? ""} 发出`
                    : ticked
                      ? "正在重新安排…"
                      : `原定 ${scheduledAt(e.plannedAt)}`}
                </span>
              </label>
            );
          })}
        </section>
      ))}
      {reasons.size > 0 && (
        <p className="text-muted-foreground">
          不能交的 {preview.unfit.length} 封：
          {[...reasons].map(([reason, count]) => `${REASONS[reason]} ${count} 封`).join("、")}
        </p>
      )}
    </>
  );
}

function HandedOff({ result }: { result: CloudHandedOff }) {
  return (
    <DialogBody className="flex flex-col gap-2 text-sm">
      <p>{result.handed.length > 0 ? `已交给云端 ${result.handed.length} 封。` : "没有邮件交给云端。"}</p>
      {result.loginFailed.map((m) => (
        <p key={m.mailboxId} className="text-destructive">
          云端登录 {m.address} 失败：{m.error}这个邮箱的邮件留在本地发送。
        </p>
      ))}
      {result.kept.length > 0 && (
        <p className="text-warning">
          {result.kept.length} 封留在本地：{[...new Set(result.kept.map((k) => k.reason))].join(" ")}
        </p>
      )}
    </DialogBody>
  );
}

/** "撤回到本地": back to approved here; one being sent that moment comes back with the next sync. */
export function WithdrawFromCloud({ withdraw, onDone }: { withdraw: CloudWithdraw; onDone?: () => void }) {
  const client = useQueryClient();
  const action = useMutation({
    mutationFn: () => call("withdrawFromCloud", withdraw),
    onSuccess: () => onDone?.(),
    onSettled: () => {
      refreshSchedule(client);
      void client.invalidateQueries({ queryKey: ["listMailboxes"] });
    },
  });
  return (
    <>
      <Button variant="ghost" size="sm" disabled={action.isPending} onClick={() => action.mutate()}>
        撤回到本地
      </Button>
      {action.data && action.data.sending > 0 && (
        <p className="w-full text-xs text-muted-foreground">
          {action.data.sending} 封正在发送，撤不回了，稍后同步结果。
        </p>
      )}
      {action.error && <p className="w-full text-xs text-destructive">未能撤回：{errorMessage(action.error)}</p>}
    </>
  );
}
