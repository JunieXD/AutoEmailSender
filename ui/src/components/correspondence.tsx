import { useMutation, useMutationState, useQuery, useQueryClient } from "@tanstack/react-query";
import { openUrl } from "@tauri-apps/plugin-opener";
import { Check, RotateCcw, Send, Undo2 } from "lucide-react";
import { useEffect, useEffectEvent, useRef, useState, useSyncExternalStore } from "react";
import { call, errorMessage } from "@/api/call";
import type { Email, Entry, MentorDetail, Message, FollowUpEntry, ReplyOutcome, Todo } from "@/api/types";
import { Attachments } from "@/components/attachments";
import { Confirm } from "@/components/batch-bar";
import { FollowUpLetter, ReplyLetter } from "@/components/reply-letter";
import { FirstLetter } from "@/components/first-letter";
import {
  contactDetail,
  dropDrafts,
  OUTCOME_ORDER,
  OUTCOMES,
  OutcomeChip,
  refreshMentors,
  refreshReplyOutcomes,
} from "@/components/mentor";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ScheduleActions, refreshSchedule, scheduledAt } from "@/components/schedule";
import { AddTodo, TodoItem } from "@/components/todos";
import { TrackingNote } from "@/components/tracking";
import { day, time } from "@/format";
import { LETTER_BODY } from "@/letter";
import { toHtml } from "@/markdown";
import { useNow } from "@/now";

/** Pending mail hides both entry buttons, while a blocked thread still explains why. */
export function followUpActions(entry: FollowUpEntry, now: number) {
  return {
    message:
      entry.reason ??
      `已发出 ${Math.max(0, Math.floor((now - (entry.previous?.sentAt ?? now)) / 86_400_000))} 天，还没回复`,
    followUp: !entry.pending && entry.reason === null,
    newLetter: !entry.pending,
  };
}

/** The middle of a mentor's page: the correspondence, or the first email being written (docs/ui.md#交互). */
export function Correspondence({ mentor }: { mentor: MentorDetail }) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<{ id: number; version: number } | null>(null);
  const [replyVersion, setReplyVersion] = useState(0);
  const [openedAt] = useState(() => Date.now());
  const [openedFirst, setOpenedFirst] = useState<{ mentorId: number; count: number | undefined } | null>(null);
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
  const emailCount = entries.data?.filter((e) => "email" in e).length;
  const newLetter = openedFirst?.mentorId === mentor.id && openedFirst.count === emailCount;
  const wasOut = useRef(out);
  useEffect(() => {
    if (wasOut.current !== undefined && out !== wasOut.current) refreshMentors(queryClient);
    wasOut.current = out;
  }, [out, queryClient]);
  const fresh = useMarkRead(mentor.id, entries.data);
  // A first letter begun stays to be finished when the mentor has mail by now, as after a merge or a
  // look-up: a draft, or what the composer keeps of a save that failed.
  const draft = useQuery({
    queryKey: ["getFirstDraft", mentor.id],
    queryFn: () => call("getFirstDraft", { id: mentor.id }),
    enabled: !mentor.trashed,
  });
  const replyDraft = useQuery({
    queryKey: ["getReplyDraft", mentor.id],
    queryFn: () => call("getReplyDraft", { id: mentor.id }),
    enabled: !mentor.trashed,
  });
  const followUpDraft = useQuery({
    queryKey: ["getFollowUpDraft", mentor.id],
    queryFn: () => call("getFollowUpDraft", { id: mentor.id }),
    enabled: !mentor.trashed,
  });
  const followUpEntry = useQuery({
    queryKey: ["getFollowUpEntry", mentor.id],
    queryFn: () => call("getFollowUpEntry", { id: mentor.id }),
    enabled: !mentor.trashed,
  });
  const follow = useMutation({
    mutationFn: () => call("createFollowUpDraft", { id: mentor.id }),
    onSuccess: (saved) => {
      queryClient.setQueryData(["getFollowUpDraft", mentor.id], saved);
      void queryClient.invalidateQueries({ queryKey: ["getFollowUpEntry", mentor.id] });
    },
    onError: () => {
      void queryClient.invalidateQueries({ queryKey: ["getFollowUpEntry", mentor.id] });
    },
  });
  useEffect(() => {
    if (emailCount !== undefined) {
      void queryClient.invalidateQueries({ queryKey: ["getFollowUpDraft", mentor.id] });
      void queryClient.invalidateQueries({ queryKey: ["getFollowUpEntry", mentor.id] });
    }
  }, [emailCount, mentor.id, queryClient]);
  const actions = followUpEntry.data ? followUpActions(followUpEntry.data, openedAt) : null;
  const lastReplySave = useMutationState({
    filters: { mutationKey: ["saveReplyDraft", mentor.id] },
    select: (m) => m.state.status,
  }).at(-1);
  const replying = replyDraft.data || (lastReplySave !== undefined && lastReplySave !== "success");
  const lastSave = useMutationState({
    filters: { mutationKey: ["saveFirstDraft", mentor.id] },
    select: (m) => m.state.status,
  }).at(-1);
  const begun = draft.data || (lastSave !== undefined && lastSave !== "success");
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
        {!mentor.trashed && (
          <div className="flex flex-col px-5 pb-4 phone:px-3">
            <AddTodo mentorId={mentor.id} />
          </div>
        )}
        {!mentor.trashed && replyDraft.isSuccess && (!replying || begun) && <FirstLetter mentor={mentor} />}
        {!mentor.trashed && replying && <ReplyLetter mentor={mentor} selected={selected} />}
      </>
    );
  }
  return (
    <>
      <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto px-5 py-6 phone:px-3 [&>*]:shrink-0">
        {groupTodos(entries.data).map((e) =>
          "todos" in e ? (
            <TodoGroup key={e.key} todos={e.todos} readOnly={mentor.trashed} />
          ) : "email" in e ? (
            <Sent
              key={`email ${e.email.id}`}
              email={e.email}
              mentorId={mentor.id}
              actions={!mentor.trashed}
              emails={mentor.emails}
              onWithdraw={() => {
                setSelected(null);
                setReplyVersion((v) => v + 1);
              }}
            />
          ) : (
            <Seen
              key={`message ${e.message.id}`}
              message={e.message}
              mentor={mentor}
              fresh={fresh.includes(e.message.id)}
              onReply={() => setSelected((old) => ({ id: e.message.id, version: (old?.version ?? 0) + 1 }))}
            />
          ),
        )}
        {!mentor.trashed && <AddTodo mentorId={mentor.id} />}
      </div>
      {/* Shown here, as the composer would load the draft again each time it came up. */}
      {!mentor.trashed &&
        (begun || newLetter ? (
          <FirstLetter mentor={mentor} />
        ) : (
          draft.error && <p className="px-5 py-4 text-sm text-destructive">草稿未能加载：{errorMessage(draft.error)}</p>
        ))}
      {!mentor.trashed && !begun && !newLetter && followUpDraft.data && <FollowUpLetter mentor={mentor} />}
      {!mentor.trashed &&
        !begun &&
        !newLetter &&
        !followUpDraft.data &&
        mentor.contact.status !== "replied" &&
        actions &&
        actions.newLetter && (
          <div className="flex flex-wrap items-center gap-2 border-t border-border px-5 py-3 phone:px-3">
            <p className="mr-auto text-sm text-muted-foreground">{actions.message}</p>
            {actions.followUp && (
              <Button variant="quiet" disabled={follow.isPending} onClick={() => follow.mutate()}>
                追问
              </Button>
            )}
            <Button
              variant="ghost"
              disabled={follow.isPending}
              onClick={() => setOpenedFirst({ mentorId: mentor.id, count: emailCount })}
            >
              写新的一封
            </Button>
            {follow.error && <p className="text-sm text-destructive">未能追问：{errorMessage(follow.error)}</p>}
          </div>
        )}
      {(followUpDraft.error ?? followUpEntry.error) && (
        <p className="px-5 py-3 text-sm text-destructive">
          追问未能加载：{errorMessage(followUpDraft.error ?? followUpEntry.error)}
        </p>
      )}
      {!mentor.trashed && (replying || entries.data.some((e) => "message" in e && e.message.kind === "reply")) && (
        <ReplyLetter key={`${mentor.id} ${replyVersion}`} mentor={mentor} selected={selected} />
      )}
    </>
  );
}

/**
 * Opening the correspondence reads the mentor's new replies: their red dot goes from the list, and
 * stays beside them here until the page is opened again. Only replies shown are read, not ones that
 * came in since the list was loaded, and those arriving while the window is hidden or behind another
 * are read when it comes back to the front. Returns those replies.
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
    mutationFn: (ids: number[]) => call("markRead", { ids }),
    onSuccess: () => {
      for (const queryKey of [
        ["listCorrespondence", mentorId],
        ["listMentors"],
        ["listTrash"],
        ["getMentor", mentorId],
        ["listConversations"],
        ["unreadCount"],
      ]) {
        void queryClient.invalidateQueries({ queryKey });
      }
    },
  });
  const read = useEffectEvent(() => markRead.mutate(unreadIds));
  const inFront = useSyncExternalStore(onFrontChange, isInFront);
  useEffect(() => {
    if (unread && inFront) read();
  }, [unread, inFront]);
  return [...fresh, ...added];
}

function isInFront(): boolean {
  return document.visibilityState === "visible" && document.hasFocus();
}

function onFrontChange(changed: () => void): () => void {
  document.addEventListener("visibilitychange", changed);
  window.addEventListener("focus", changed);
  window.addEventListener("blur", changed);
  return () => {
    document.removeEventListener("visibilitychange", changed);
    window.removeEventListener("focus", changed);
    window.removeEventListener("blur", changed);
  };
}

function onTheWay(e: Email): boolean {
  return e.state === "approved" || e.state === "sending";
}

/** One of the user's emails, on the right like their messages in a chat. */
function Sent({
  email: e,
  mentorId,
  actions,
  emails,
  onWithdraw,
}: {
  email: Email;
  mentorId: number;
  actions: boolean;
  /** The mentor's addresses, the first of which an email sent again goes to. */
  emails: string[];
  onWithdraw: () => void;
}) {
  const queryClient = useQueryClient();
  const act = useMutation({
    mutationFn: (op: "withdrawEmail" | "retryEmail" | "confirmUnsentEmail" | "resendEmail") => call(op, { id: e.id }),
    onSuccess: (_, op) => {
      refreshSchedule(queryClient);
      // Read again when the composer comes back, as the draft it shows is this email.
      if (op === "withdrawEmail") {
        dropDrafts(queryClient, [mentorId], e.kind);
        onWithdraw();
      }
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["listCorrespondence", mentorId] }),
  });
  const sendTo = emails.at(0);
  // Which address it went to is worth saying when the mentor has several.
  const [text, tone] = status(e, emails.length > 1);
  return (
    <article className="flex flex-col items-end gap-1.5">
      <div className="max-w-[min(42rem,88%)] rounded-[18px] rounded-br-[6px] bg-mine px-4 py-3">
        <h3 className="mb-2 font-semibold">{e.subject}</h3>
        <LetterBody markdown={e.bodyMd} />
        {e.attachments.length > 0 && (
          <div className="mt-3">
            <Attachments files={e.attachments} />
          </div>
        )}
      </div>
      <p className={`max-w-[min(42rem,88%)] text-right text-sm ${tone}`}>{text}</p>
      {e.tracking && <TrackingNote tracking={e.tracking} />}
      {actions && (
        <div className="flex flex-wrap justify-end gap-1">
          {e.state === "approved" && <ScheduleActions id={e.id} initial={e.plannedAt ?? undefined} />}
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
                  {e.cancelReason === null && <Send strokeWidth={1.6} />}
                  {e.cancelReason === null ? "重新发送" : "确认没发出"}
                </Button>
              }
              message={
                e.cancelReason === null
                  ? "确认这封邮件没发出？如果之前已经发出，导师会收到两封。"
                  : `已取消：${e.cancelReason}。确认这封邮件没发出后，将结束这封邮件，不再发送。`
              }
              action={e.cancelReason === null ? "重新发送" : "确认没发出"}
              onConfirm={() => act.mutate(e.cancelReason === null ? "retryEmail" : "confirmUnsentEmail")}
            />
          )}
          {e.state === "bounced" && !e.resent && sendTo && (
            <Confirm
              trigger={
                <Button variant="ghost" disabled={act.isPending}>
                  <Send strokeWidth={1.6} />
                  重新发送
                </Button>
              }
              message={
                sendTo.toLowerCase() === e.toAddress.toLowerCase()
                  ? `会再发到退回它的地址 ${sendTo}。地址有误时，先在资料里改正，或把能用的地址设为发信地址。`
                  : `发一封内容和附件都相同的新邮件到 ${sendTo}。`
              }
              action="重新发送"
              onConfirm={() => act.mutate("resendEmail")}
            />
          )}
          {(e.state === "approved" || e.state === "failed") && e.resendOf === null && (
            <Button variant="ghost" disabled={act.isPending} onClick={() => act.mutate("withdrawEmail")}>
              <Undo2 strokeWidth={1.6} />
              改回草稿
            </Button>
          )}
        </div>
      )}
      {act.error && (
        <p className="text-sm text-destructive">
          未能
          {act.variables === "withdrawEmail"
            ? "改回草稿"
            : act.variables === "confirmUnsentEmail"
              ? "确认没发出"
              : e.state === "failed"
                ? "重试"
                : "重新发送"}
          ：{errorMessage(act.error)}
        </p>
      )}
    </article>
  );
}

/** What an email's state reads as, and its colour. A bounce always says which address it came back from. */
function status(e: Email, several: boolean): [string, string] {
  if (e.state === "approved")
    return [
      e.latestAt !== null && e.plannedAt !== null ? `定时 ${scheduledAt(e.plannedAt)}` : "待发送",
      "text-muted-foreground",
    ];
  if (e.state === "sending")
    return [
      e.cancelReason === null ? "发送中" : `发送中；已取消：${e.cancelReason}，等待发送结果。`,
      "text-muted-foreground",
    ];
  if (e.state === "sent") {
    const to = several ? `，发到 ${e.toAddress}` : "";
    return [`${day(e.sentAt)} ${time(e.sentAt)} 由 ${e.fromAddress} 发出${to}`, "text-muted-foreground"];
  }
  if (e.state === "maybe_sent") {
    const to = several ? `（发到 ${e.toAddress}）` : "";
    const next =
      e.cancelReason === null
        ? "确认导师未收到时，可重新发送。"
        : `已取消：${e.cancelReason}。确认没发出后，不再发送。`;
    return [`可能已发出${to}：${e.error}${next}`, "text-warning"];
  }
  if (e.state === "canceled") return [`已取消：${e.cancelReason}`, "text-muted-foreground"];
  if (e.state === "failed") return [`发送失败：${e.error}`, "text-destructive"];
  return [`退信（发到 ${e.toAddress}）：${e.error}`, "text-warning"];
}

type Shown = Exclude<Entry, { todo: Todo }> | { todos: Todo[]; key: string };

/** Todos next to each other show as one list. */
function groupTodos(entries: Entry[]): Shown[] {
  const shown: Shown[] = [];
  for (const e of entries) {
    const last = shown.at(-1);
    if (!("todo" in e)) shown.push(e);
    else if (last && "todos" in last) last.todos.push(e.todo);
    else {
      // Keyed by what it follows, so deleting its first todo keeps the others' editors open.
      const after = !last ? "start" : "email" in last ? `email ${last.email.id}` : `message ${last.message.id}`;
      shown.push({ todos: [e.todo], key: `todos after ${after}` });
    }
  }
  return shown;
}

function TodoGroup({ todos, readOnly }: { todos: Todo[]; readOnly: boolean }) {
  const now = useNow();
  return (
    <ul
      aria-label="待办"
      className="w-full max-w-[min(42rem,88%)] self-start divide-y divide-border rounded-lg border border-border"
    >
      {todos.map((t) => (
        <li key={t.id}>
          <TodoItem todo={t} readOnly={readOnly} now={now} />
        </li>
      ))}
    </ul>
  );
}

/** Mail seen in the mailbox: the mentor's on the left like their messages in a chat, the user's own from elsewhere on the right. */
function Seen({
  message: m,
  mentor,
  fresh,
  onReply,
}: {
  message: Message;
  mentor: MentorDetail;
  fresh: boolean;
  onReply: () => void;
}) {
  const [quoted, setQuoted] = useState(false);
  const [opened, setOpened] = useState(false);
  const queryClient = useQueryClient();
  const add = useMutation({
    mutationFn: () => call("addMentorEmail", { id: mentor.id, address: m.fromAddress }),
    onSuccess: async (detail) => {
      // A load still under way may have read the mentor before the address was added.
      await queryClient.cancelQueries({ queryKey: ["getMentor", mentor.id] });
      queryClient.setQueryData(["getMentor", mentor.id], detail);
    },
  });
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
  // Someone writing for the mentor, such as a secretary, or from an address not in their profile, is named.
  const known = mentor.emails.some((e) => e.toLowerCase() === m.fromAddress);
  const from = mine || known ? "" : `，来自 ${m.fromAddress}`;
  return (
    <article className={`flex flex-col gap-1.5 ${mine ? "items-end" : "items-start"}`}>
      <div
        className={`max-w-[min(42rem,88%)] rounded-[18px] px-4 py-3 ${mine ? "rounded-br-[6px] bg-mine" : "rounded-bl-[6px] bg-muted"}`}
      >
        {m.subject && <h3 className="mb-2 font-semibold">{m.subject}</h3>}
        <Text text={m.text} />
        {m.truncated && (
          <p className="mt-2 text-sm text-muted-foreground">
            邮件较大，只读了开头，可能没有显示全；完整内容请到邮箱里查看。
          </p>
        )}
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
        {m.kind === "reply" && (
          <ReplyOutcomeMenu
            messageId={m.id}
            outcome={m.outcome}
            marking={m.marking?.state === "marking"}
            readOnly={mentor.trashed}
          />
        )}
        {m.kind === "reply" && !mentor.trashed && (
          <Button variant="quiet" size="sm" disabled={!m.canReply} onClick={onReply}>
            回复
          </Button>
        )}
        {!mine && !known && !mentor.trashed && (
          <Button variant="quiet" size="sm" disabled={add.isPending} onClick={() => add.mutate()}>
            加为导师的邮箱
          </Button>
        )}
      </p>
      {m.kind === "reply" && m.marking?.state === "failed" && !mentor.trashed && (
        <AiFailed messageId={m.id} reason={m.marking.reason} retry="retryReplyOutcome" label="AI 未能标出" />
      )}
      {m.kind === "reply" && m.todoExtraction?.state === "extracting" && !mentor.trashed && (
        <p className="text-sm text-faint">AI 正在找回信里要你做的事…</p>
      )}
      {m.kind === "reply" && m.todoExtraction?.state === "failed" && !mentor.trashed && (
        <AiFailed messageId={m.id} reason={m.todoExtraction.reason} retry="retryReplyTodos" label="AI 未能提取待办" />
      )}
      {m.kind === "reply" && !m.canReply && !mentor.trashed && (
        <p className="text-sm text-muted-foreground">这封信缺少邮件编号，无法在原线程里回复，请到邮箱里回复</p>
      )}
      {add.error && <p className="text-sm text-destructive">未能加为导师的邮箱：{errorMessage(add.error)}</p>}
      {m.kind === "sent" && m.bounce && <p className="text-sm text-warning">退信：{m.bounce}</p>}
    </article>
  );
}

/** What a reply said, which the user can set or change (docs/ui.md#交互). */
function ReplyOutcomeMenu({
  messageId,
  outcome,
  marking,
  readOnly,
}: {
  messageId: number;
  outcome: ReplyOutcome | null;
  marking: boolean;
  readOnly: boolean;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const set = useMutation({
    mutationFn: (o: ReplyOutcome) => call("setReplyOutcome", { messageId, outcome: o }),
    onSettled: () => refreshReplyOutcomes(queryClient),
  });
  if (readOnly) return outcome && <OutcomeChip outcome={outcome} />;
  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          render={
            outcome ? (
              <button type="button" aria-label={`回信结果：${OUTCOMES[outcome]}，点击修改`} className="flex" />
            ) : (
              <Button variant={marking ? "ghost" : "quiet"} size="sm" />
            )
          }
          disabled={set.isPending}
        >
          {outcome ? <OutcomeChip outcome={outcome} /> : marking ? "AI 正在标记…" : "标记结果"}
        </PopoverTrigger>
        <PopoverContent align="start" className="w-36 p-1.5">
          {OUTCOME_ORDER.map((o) => (
            <Button
              key={o}
              variant="ghost"
              className="w-full justify-between"
              onClick={() => {
                setOpen(false);
                if (o !== outcome) set.mutate(o);
              }}
            >
              {OUTCOMES[o]}
              {o === outcome && <Check className="size-4" strokeWidth={1.8} />}
            </Button>
          ))}
        </PopoverContent>
      </Popover>
      {set.error && <span className="text-destructive">未能保存：{errorMessage(set.error)}</span>}
    </>
  );
}

/** A reply the AI could not mark or take todos from, which can be tried again. */
function AiFailed({
  messageId,
  reason,
  retry,
  label,
}: {
  messageId: number;
  reason: string;
  retry: "retryReplyOutcome" | "retryReplyTodos";
  label: string;
}) {
  const queryClient = useQueryClient();
  const again = useMutation({
    mutationFn: () => call(retry, { messageId }),
    onSettled: () => refreshReplyOutcomes(queryClient),
  });
  return (
    <p className="flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
      {label}：{reason}
      <Button variant="quiet" size="sm" disabled={again.isPending} onClick={() => again.mutate()}>
        重试
      </Button>
      {again.error && <span className="text-destructive">{errorMessage(again.error)}</span>}
    </p>
  );
}

// Subjects that already say they are an automatic reply.
const AUTOMATIC = /^(自动回复|自动答复|automatic reply|auto[- ]?reply|out of office)/i;

/** A letter's body as the app shows it, its links opened in the system's browser or mail app. */
export function LetterBody({ markdown }: { markdown: string }) {
  return (
    <div
      className={LETTER_BODY}
      dangerouslySetInnerHTML={{ __html: toHtml(markdown) }}
      // The letter keeps only web and mail links (docs/design.md#邮件格式).
      onClick={openLinks}
    />
  );
}

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
export function openLinks(event: React.MouseEvent) {
  const link = event.target instanceof Element ? event.target.closest("a") : null;
  if (!link) return;
  event.preventDefault();
  void openUrl(link.href);
}
