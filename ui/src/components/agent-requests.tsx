import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listen } from "@tauri-apps/api/event";
import { useEffect } from "react";
import { call, errorMessage } from "@/api/call";
import type { AgentRequest, ConfirmationLetter, SendAt } from "@/api/types";
import { LetterBody } from "@/components/correspondence";
import { scheduledAt, scheduleText } from "@/components/schedule";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { fileSize } from "@/format";
import { letterHtml, letterText } from "@/letter";

/**
 * What an Agent asked to do that needs the user's say (docs/ui.md 设置 › 应用 › 手机和 Agent), one
 * request at a time, oldest first. The core drops a request after five minutes or when its Agent
 * goes away, and says so with the same event.
 */
export function AgentRequests() {
  const queryClient = useQueryClient();
  const requests = useQuery({ queryKey: ["listAgentRequests"], queryFn: () => call("listAgentRequests", {}) });
  useEffect(() => {
    const stop = listen("agent-requests-changed", () => {
      void queryClient.invalidateQueries({ queryKey: ["listAgentRequests"] });
    });
    return () => void stop.then((f) => f());
  }, [queryClient]);
  const [request, ...queued] = requests.data ?? [];
  return request ? <RequestDialog key={request.id} request={request} queued={queued.length} /> : null;
}

// docs/ui.md: at most five of the mentors are named, the rest counted.
const SHOWN = 5;

function RequestDialog({ request, queued }: { request: AgentRequest; queued: number }) {
  const queryClient = useQueryClient();
  const style = useQuery({ queryKey: ["getLetterStyle"], queryFn: () => call("getLetterStyle", {}) });
  const { action, targets, letters } = request.description;
  const answer = useMutation({
    mutationFn: (allow: boolean) => {
      const s = style.data;
      // What goes out is the letter shown here, rendered as the app renders every letter.
      const rendered =
        allow && s ? letters.map((l) => ({ html: letterHtml(l.bodyMd, s), text: letterText(l.bodyMd) })) : undefined;
      return call("answerAgentRequest", { id: request.id, allow, rendered });
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["listAgentRequests"] }),
  });
  // An answered request stays on screen until the list comes back without it.
  const busy = answer.isPending || answer.isSuccess;
  // A task's letters all go by the task's schedule, said once above them.
  const [first] = letters;
  const sharedTime =
    letters.length > 1 && first && letters.every((l) => sendText(l.sendAt) === sendText(first.sendAt))
      ? sendText(first.sendAt)
      : null;
  return (
    // Closing it is a no: the Agent hears it was refused and can ask again.
    <Dialog open onOpenChange={(open) => !open && !busy && answer.mutate(false)}>
      <DialogContent className={letters.length > 0 ? "w-[600px]" : "w-[480px]"}>
        <DialogHeader>Agent 请求</DialogHeader>
        <DialogBody className="space-y-3 py-4">
          <p className="text-base">{action}</p>
          {/* Names and addresses come from the Agent or from web pages, so they stay in their own
              boxes, apart from what the app itself says. */}
          {targets.length > 0 && (
            <ul className="divide-y divide-border rounded-lg border border-border text-sm">
              {targets.slice(0, SHOWN).map((target, i) => (
                <li key={i} className="px-3 py-1.5 wrap-anywhere">
                  {target}
                </li>
              ))}
              {targets.length > SHOWN && (
                <li className="px-3 py-1.5 text-muted-foreground">等 {targets.length - SHOWN} 位</li>
              )}
            </ul>
          )}
          {letters.length === 1 && first && <Letter letter={first} time />}
          {letters.length > 1 && (
            <div className="space-y-2">
              {sharedTime && <p className="text-sm">{sharedTime}</p>}
              <ul className="max-h-80 divide-y divide-border overflow-y-auto rounded-lg border border-border text-sm">
                {letters.map((l, i) => (
                  <li key={i}>
                    <details>
                      <summary className="cursor-pointer px-3 py-2 wrap-anywhere">
                        {l.mentorName}：{l.subject}
                      </summary>
                      <div className="px-3 pb-3">
                        <Letter letter={l} time={!sharedTime} />
                      </div>
                    </details>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {style.error && <p className="text-sm text-destructive">信件样式未能读取：{errorMessage(style.error)}</p>}
          <p className="text-sm text-muted-foreground">
            这是你接入的 Agent 要做的事，允许后马上执行。5 分钟内不回应，就当作拒绝。
            {queued > 0 && `之后还有 ${queued} 条请求。`}
          </p>
          {answer.error && <p className="text-sm text-destructive">{errorMessage(answer.error)}</p>}
        </DialogBody>
        <DialogFooter className="justify-end">
          <Button variant="ghost" onClick={() => answer.mutate(false)} disabled={busy}>
            拒绝
          </Button>
          <Button onClick={() => answer.mutate(true)} disabled={busy || (letters.length > 0 && !style.data)}>
            允许
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function sendText(at: SendAt): string {
  if (at.kind === "now") return "立即发出";
  if (at.kind === "at") return `${scheduledAt(at.at)} 发出`;
  return at.schedule.kind === "now" ? "立即发出" : `按任务的发送安排：${scheduleText(at.schedule)}`;
}

/** A letter as it will go out; everything in it came from the Agent or the data. */
function Letter({ letter: l, time }: { letter: ConfirmationLetter; time: boolean }) {
  return (
    <div className="space-y-2 text-sm">
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-muted-foreground">收件人</dt>
        <dd className="wrap-anywhere">
          {l.mentorName} {l.address ?? "（未填写邮箱）"}
        </dd>
        {l.mailboxAddress && (
          <>
            <dt className="text-muted-foreground">发件邮箱</dt>
            <dd className="wrap-anywhere">{l.mailboxAddress}</dd>
          </>
        )}
        {time && (
          <>
            <dt className="text-muted-foreground">时间</dt>
            <dd>{sendText(l.sendAt)}</dd>
          </>
        )}
        <dt className="text-muted-foreground">主题</dt>
        <dd className="wrap-anywhere">{l.subject}</dd>
        {l.attachments.length > 0 && (
          <>
            <dt className="text-muted-foreground">附件</dt>
            <dd className="wrap-anywhere">
              {l.attachments.map((a) => `${a.fileName}（${fileSize(a.size)}）`).join("、")}
            </dd>
          </>
        )}
      </dl>
      <div className="max-h-64 overflow-y-auto rounded-lg border border-border px-4 py-3">
        <LetterBody markdown={l.bodyMd} />
      </div>
      {l.previous && (
        <p className="text-muted-foreground">
          正文后附上 {l.previous.fromName ?? l.previous.fromAddress} {scheduledAt(l.previous.sentAt)} 来信的引用。
        </p>
      )}
    </div>
  );
}
