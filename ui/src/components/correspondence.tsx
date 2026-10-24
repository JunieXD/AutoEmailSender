import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { openUrl } from "@tauri-apps/plugin-opener";
import { RotateCcw, Send, Undo2 } from "lucide-react";
import { useEffect, useEffectEvent, useRef, useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { Email, Entry, MentorDetail, Message } from "@/api/types";
import { Attachments } from "@/components/attachments";
import { Confirm } from "@/components/batch-bar";
import { FirstLetter } from "@/components/first-letter";
import { contactDetail, refreshMentors } from "@/components/mentor";
import { Button } from "@/components/ui/button";
import { day, time } from "@/format";
import { LETTER_BODY } from "@/letter";
import { toHtml } from "@/markdown";

/** The middle of a mentor's page: the correspondence, or the first email being written (docs/ui.md#交互). */
export function Correspondence({ mentor }: { mentor: MentorDetail }) {
  const queryClient = useQueryClient();
  const entries = useQuery({
    queryKey: ["listCorrespondence", mentor.id],
    queryFn: () => call("listCorrespondence", { id: mentor.id }),
    // Follows emails on their way out until they are out.
    refetchInterval: (query) => (query.state.data?.some((e) => "email" in e && onTheWay(e.email)) ? 1000 : false),
  });
  // An email going out changes the mentor's contact status in the header and the list. Counted, as
  // one may go out between two reads and never be seen on its way.
  const out = entries.data?.filter(
    (e) => "email" in e && (e.email.state === "sent" || e.email.state === "maybe_sent" || e.email.state === "bounced"),
  ).length;
  const wasOut = useRef(out);
  useEffect(() => {
    if (wasOut.current !== undefined && out !== wasOut.current) refreshMentors(queryClient);
    wasOut.current = out;
  }, [out, queryClient]);
  const fresh = useMarkRead(mentor.id, entries.data);
  if (entries.error) {
    return <p className="px-5 py-4 text-sm text-destructive">往来未能加载：{errorMessage(entries.error)}</p>;
  }
  if (!entries.data) return null;
  if (entries.data.length === 0) {
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
      {entries.data.map((e) =>
        "email" in e ? (
          <Sent key={`email ${e.email.id}`} email={e.email} mentorId={mentor.id} actions={!mentor.trashed} />
        ) : (
          <Seen
            key={`message ${e.message.id}`}
            message={e.message}
            mentor={mentor}
            fresh={fresh.includes(e.message.id)}
          />
        ),
      )}
    </div>
  );
}

/**
 * Opening the correspondence reads the mentor's new replies: their red dot goes from the list, and
 * stays beside them here until the page is opened again. Returns those replies.
 */
function useMarkRead(mentorId: number, entries: Entry[] | undefined): number[] {
  const queryClient = useQueryClient();
  const unreadIds = (entries ?? []).flatMap((e) =>
    "message" in e && e.message.kind === "reply" && e.message.unread ? [e.message.id] : [],
  );
  const unread = unreadIds.join();
  // Replies are marked read as soon as they are seen, so which were new is kept here.
  const [seen, setSeen] = useState<{ mentorId: number; ids: number[] }>({ mentorId, ids: [] });
  const fresh = seen.mentorId === mentorId ? seen.ids : [];
  const added = unreadIds.filter((id) => !fresh.includes(id));
  if (seen.mentorId !== mentorId || added.length > 0) setSeen({ mentorId, ids: [...fresh, ...added] });
  const markRead = useMutation({
    mutationFn: () => call("markRead", { id: mentorId }),
    onSuccess: () => {
      for (const queryKey of [["listMentors"], ["listTrash"], ["getMentor", mentorId]]) {
        void queryClient.invalidateQueries({ queryKey });
      }
    },
  });
  const read = useEffectEvent(() => markRead.mutate());
  useEffect(() => {
    if (unread) read();
  }, [unread]);
  return [...fresh, ...added];
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
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["listCorrespondence", mentorId] }),
  });
  const [text, tone] = status(e);
  return (
    <article className="flex flex-col items-end gap-1.5">
      <div className="max-w-[min(42rem,88%)] rounded-[18px] rounded-br-[6px] bg-mine px-4 py-3">
        <h3 className="mb-2 font-semibold">{e.subject}</h3>
        <div
          className={LETTER_BODY}
          dangerouslySetInnerHTML={{ __html: toHtml(e.bodyMd) }}
          // The letter keeps only web and mail links (docs/design.md#邮件格式).
          onClick={openLinks}
        />
        {e.attachments.length > 0 && (
          <div className="mt-3">
            <Attachments files={e.attachments} />
          </div>
        )}
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

/** Mail seen in the mailbox: the mentor's on the left like their messages in a chat, the user's own from elsewhere on the right. */
function Seen({ message: m, mentor, fresh }: { message: Message; mentor: MentorDetail; fresh: boolean }) {
  const [quoted, setQuoted] = useState(false);
  const [opened, setOpened] = useState(false);
  const mine = m.kind === "sent";
  if (m.kind === "auto_reply" && !opened) {
    return (
      <button
        type="button"
        className="max-w-[88%] self-start truncate rounded-md px-1 text-left text-sm text-faint hover:text-muted-foreground"
        onClick={() => setOpened(true)}
      >
        {m.subject && AUTOMATIC.test(m.subject) ? m.subject : `自动回复${m.subject ? `：${m.subject}` : ""}`}
      </button>
    );
  }
  // Someone writing for the mentor, such as a secretary, is named.
  const from = mine || m.fromAddress === mentor.email?.toLowerCase() ? "" : `，来自 ${m.fromAddress}`;
  return (
    <article className={`flex flex-col gap-1.5 ${mine ? "items-end" : "items-start"}`}>
      <div
        className={`max-w-[min(42rem,88%)] rounded-[18px] px-4 py-3 ${mine ? "rounded-br-[6px] bg-mine" : "rounded-bl-[6px] bg-muted"}`}
      >
        {m.subject && <h3 className="mb-2 font-semibold">{m.subject}</h3>}
        <Text text={m.text} />
        {m.quote && (
          <>
            <button
              type="button"
              className="mt-2 text-sm text-muted-foreground hover:text-foreground"
              aria-expanded={quoted}
              onClick={() => setQuoted(!quoted)}
            >
              {quoted ? "收起引用的原文" : "显示引用的原文"}
            </button>
            {quoted && (
              <div className="mt-2 border-l-2 border-border pl-3 text-muted-foreground">
                <Text text={m.quote} />
              </div>
            )}
          </>
        )}
      </div>
      <p className={`flex max-w-[min(42rem,88%)] items-center gap-1.5 text-sm ${mine ? "text-right" : ""}`}>
        {fresh && <span className="size-2 shrink-0 rounded-full bg-unread" aria-label="新回信" />}
        <span className="text-muted-foreground">
          {day(m.sentAt)} {time(m.sentAt)}
          {mine ? " 在别处发出" : from}
        </span>
      </p>
      {m.kind === "sent" && m.bounce && <p className="text-sm text-warning">退信：{m.bounce}</p>}
    </article>
  );
}

// Subjects that already say they are an automatic reply.
const AUTOMATIC = /^(自动回复|自动答复|automatic reply|auto[- ]?reply|out of office)/i;

/** Plain text as written, with web addresses that open in the system's browser. */
function Text({ text }: { text: string }) {
  const parts = text.split(LINK);
  return (
    <div className="leading-relaxed break-words whitespace-pre-wrap" onClick={openLinks}>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <a key={i} href={part.startsWith("www.") ? `https://${part}` : part} className="text-brand-text underline">
            {part}
          </a>
        ) : (
          part
        ),
      )}
    </div>
  );
}

// Ends where the characters an address may have do, such as at Chinese punctuation, and not on a
// full stop or comma after it.
const LINK = /((?:https?:\/\/|www\.)[\w\-.~:/?#[\]@!$&'()*+,;=%]*[\w\-~/#=%&])/;

/** Links open in the system's browser or mail app, not in place of the app. */
function openLinks(event: React.MouseEvent) {
  const link = event.target instanceof Element ? event.target.closest("a") : null;
  if (!link) return;
  event.preventDefault();
  void openUrl(link.href);
}
