import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Send, Undo2 } from "lucide-react";
import { useEffect, useRef } from "react";
import { call, errorMessage } from "@/api/call";
import type { Email, MentorDetail } from "@/api/types";
import { Confirm } from "@/components/batch-bar";
import { FirstLetter } from "@/components/first-letter";
import { contactDetail, refreshMentors } from "@/components/mentor";
import { Button } from "@/components/ui/button";
import { day, time } from "@/format";
import { LETTER_BODY } from "@/letter";
import { toHtml } from "@/markdown";

/** The middle of a mentor's page: the emails sent, or the first one being written (docs/ui.md#交互). */
export function Correspondence({ mentor }: { mentor: MentorDetail }) {
  const queryClient = useQueryClient();
  const emails = useQuery({
    queryKey: ["listEmails", mentor.id],
    queryFn: () => call("listEmails", { id: mentor.id }),
    // Follows emails on their way out until they are out.
    refetchInterval: (query) => (query.state.data?.some(onTheWay) ? 1000 : false),
  });
  const pending = emails.data?.some(onTheWay) ?? false;
  const wasPending = useRef(pending);
  useEffect(() => {
    // Sending changes the mentor's contact status in the header and the list.
    if (wasPending.current && !pending) refreshMentors(queryClient);
    wasPending.current = pending;
  }, [pending, queryClient]);
  if (emails.error) {
    return <p className="px-5 py-4 text-sm text-destructive">往来未能加载：{errorMessage(emails.error)}</p>;
  }
  if (!emails.data) return null;
  if (emails.data.length === 0) {
    return (
      <>
        <div className="grid flex-1 place-items-center p-6 text-center text-sm text-muted-foreground">
          {mentor.contact.status === "none" ? `尚未联系${mentor.name}老师。` : contactDetail(mentor.contact)}
        </div>
        {!mentor.trashed && <FirstLetter mentor={mentor} />}
      </>
    );
  }
  return (
    <div className="flex flex-col gap-6 px-5 py-6 phone:px-3">
      {emails.data.map((e) => (
        <Sent key={e.id} email={e} mentorId={mentor.id} actions={!mentor.trashed} />
      ))}
    </div>
  );
}

function onTheWay(e: Email): boolean {
  return e.state === "approved" || e.state === "sending";
}

/** One of the user's emails, on the right like their messages in a chat. */
function Sent({ email: e, mentorId, actions }: { email: Email; mentorId: number; actions: boolean }) {
  const queryClient = useQueryClient();
  const act = useMutation({
    mutationFn: (op: "withdrawEmail" | "retryEmail") => call(op, { id: e.id }),
    onSuccess: (_, op) => {
      // Read again when the composer comes back, as the draft it shows is this email.
      if (op === "withdrawEmail") queryClient.removeQueries({ queryKey: ["getFirstDraft", mentorId] });
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["listEmails", mentorId] }),
  });
  const [text, tone] = status(e);
  return (
    <article className="flex flex-col items-end gap-1.5">
      <div className="max-w-[min(42rem,88%)] rounded-[18px] rounded-br-[6px] bg-mine px-4 py-3">
        <h3 className="mb-2 font-semibold">{e.subject}</h3>
        <div className={LETTER_BODY} dangerouslySetInnerHTML={{ __html: toHtml(e.bodyMd) }} />
      </div>
      <p className={`max-w-[min(42rem,88%)] text-right text-sm ${tone}`}>{text}</p>
      {actions && (
        <div className="flex gap-1">
          {e.state === "failed" && (
            <Button variant="ghost" disabled={act.isPending} onClick={() => act.mutate("retryEmail")}>
              <RotateCcw strokeWidth={1.6} />
              重试
            </Button>
          )}
          {e.state === "maybe_sent" && (
            <Confirm
              trigger={
                <Button variant="ghost" disabled={act.isPending}>
                  <Send strokeWidth={1.6} />
                  重新发送
                </Button>
              }
              message="确认这封邮件未发出？如果之前已经发出，导师会收到两封。"
              action="重新发送"
              onConfirm={() => act.mutate("retryEmail")}
            />
          )}
          {(e.state === "approved" || e.state === "failed") && (
            <Button variant="ghost" disabled={act.isPending} onClick={() => act.mutate("withdrawEmail")}>
              <Undo2 strokeWidth={1.6} />
              改回草稿
            </Button>
          )}
        </div>
      )}
      {act.error && (
        <p className="text-sm text-destructive">
          未能{act.variables === "withdrawEmail" ? "改回草稿" : e.state === "failed" ? "重试" : "重新发送"}：
          {errorMessage(act.error)}
        </p>
      )}
    </article>
  );
}

/** What an email's state reads as, and its colour. */
function status(e: Email): [string, string] {
  if (e.state === "approved") return ["待发送", "text-muted-foreground"];
  if (e.state === "sending") return ["发送中", "text-muted-foreground"];
  if (e.state === "sent")
    return [`${day(e.sentAt)} ${time(e.sentAt)} 由 ${e.fromAddress} 发出`, "text-muted-foreground"];
  if (e.state === "maybe_sent") return [`可能已发出：${e.error}确认导师未收到时，可重新发送。`, "text-warning"];
  if (e.state === "failed") return [`发送失败：${e.error}`, "text-destructive"];
  return [`退信：${e.error}`, "text-warning"];
}
