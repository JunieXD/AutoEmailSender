import type { QueryClient } from "@tanstack/react-query";
import type { Affiliation, Contact, ContactStatus, DraftKind, ReplyOutcome } from "@/api/types";
import { refreshTasks } from "@/components/task-cache";
import { day } from "@/format";

const AVATARS = ["bg-avatar-1", "bg-avatar-2", "bg-avatar-3", "bg-avatar-4", "bg-avatar-5"];

/** The name's first character on a tint chosen by id, so a mentor looks the same everywhere. */
export function Avatar({ id, name }: { id: number; name: string }) {
  return (
    <span
      className={`grid size-9 shrink-0 place-items-center rounded-full text-sm font-medium ${AVATARS[id % AVATARS.length]}`}
    >
      {name.slice(0, 1)}
    </span>
  );
}

/** Where in a university an affiliation is, as in "北京大学 计算机学院 软件系". */
export function placeOf(a: Affiliation): string {
  return [a.university, a.school, a.department].filter(Boolean).join(" ");
}

export function Missing({ children }: { children: string }) {
  return <span className="text-faint">{children}</span>;
}

const STATUS_STYLE: Record<ContactStatus, [string, string]> = {
  none: ["未联系", "border border-input"],
  contacted: ["已联系", "bg-info"],
  replied: ["已回复", "bg-success"],
  bounced: ["退信", "bg-warning"],
};

/**
 * The contact status after its coloured dot, with a red dot for a new reply and, when known, what
 * the mentor's latest reply said.
 */
export function ContactLabel({
  contact,
  outcome = null,
  className = "",
}: {
  contact: Contact;
  outcome?: ReplyOutcome | null;
  className?: string;
}) {
  const [label, dot] = STATUS_STYLE[contact.status];
  return (
    <div className={`flex items-center gap-1.5 ${className}`}>
      <span className={`size-2 shrink-0 rounded-full ${dot}`} />
      {label}
      {contact.status === "replied" && contact.unread && (
        <span className="size-2 shrink-0 rounded-full bg-unread" aria-label="有新回信" />
      )}
      {outcome && <OutcomeChip outcome={outcome} />}
    </div>
  );
}

export const OUTCOME_ORDER: ReplyOutcome[] = ["interested", "no_slot", "declined", "unclear"];

export const OUTCOMES: Record<ReplyOutcome, string> = {
  interested: "有意向",
  no_slot: "无名额",
  declined: "拒绝",
  unclear: "无法判断",
};

/** What a reply said, in green when the mentor is open to the student. */
export function OutcomeChip({ outcome }: { outcome: ReplyOutcome }) {
  return (
    <span
      className={`shrink-0 rounded-full px-2 text-xs leading-5 ${
        outcome === "interested" ? "bg-success-soft text-success" : "bg-muted text-muted-foreground"
      }`}
    >
      {OUTCOMES[outcome]}
    </span>
  );
}

/** How often and when, such as "联系 2 次，昨天回复"; null for a mentor never contacted. */
export function contactDetail(contact: Contact): string | null {
  if (contact.status === "none") return null;
  // Nothing for zero: a mentor who wrote first, or was only answered elsewhere, has no counted email.
  const times = contact.count > 0 ? `联系 ${contact.count} 次，` : "";
  // A bounce can arrive days later, so a bounced mentor shows when the mail went out.
  return contact.status === "replied"
    ? `${times}${day(contact.lastReplyAt)}回复`
    : `${times}${day(contact.lastOutAt)}发出`;
}

export const BASIS_CHANGED = "材料或意向研究方向改过，可以重新分析";

/** After a reply's outcome was set or marked, or its marking failed or was retried. */
export function refreshReplyOutcomes(queryClient: QueryClient) {
  for (const queryKey of [["replyProcessing"], ["listCorrespondence"], ["listMentors"]]) {
    void queryClient.invalidateQueries({ queryKey });
  }
}

/**
 * After mentors are trashed, restored or deleted, or new mail came, every view of them is out of
 * date, their correspondence too.
 */
export function refreshMentors(queryClient: QueryClient) {
  refreshTasks(queryClient);
  for (const queryKey of [
    ["matchingProgress"],
    ["planMatching"],
    ["listMentors"],
    ["mentorFilterOptions"],
    ["listTrash"],
    ["getMentor"],
    ["listCorrespondence"],
    ["getReplyTarget"],
    ["getReplyDraft"],
    ["getFollowUpDraft"],
    ["getFollowUpEntry"],
    ["listConversations"],
    ["listTodos"],
    ["unreadCount"],
  ]) {
    void queryClient.invalidateQueries({ queryKey });
  }
}

/**
 * Replaced drafts must not come back from the cache, a failed save or an AI answer. Withdrawal
 * replaces that kind's draft; trashing and merging replace every draft of the mentor.
 */
export function dropDrafts(queryClient: QueryClient, ids: number[], kind: DraftKind | "all" = "all") {
  const mutations = queryClient.getMutationCache();
  for (const id of ids) {
    if (kind === "all" || kind === "reply") {
      queryClient.removeQueries({ queryKey: ["getReplyDraft", id] });
      for (const m of mutations.findAll({ mutationKey: ["writeReply", id] })) mutations.remove(m);
      for (const m of mutations.findAll({ mutationKey: ["factCheckEmail", "getReplyDraft", id] })) mutations.remove(m);
      for (const m of mutations.findAll({ mutationKey: ["saveReplyDraft", id], status: "error" })) mutations.remove(m);
    }
    if (kind === "all" || kind === "follow_up") {
      queryClient.removeQueries({ queryKey: ["getFollowUpDraft", id] });
      for (const key of [
        ["saveFollowUpDraft", id],
        ["writeFollowUp", id],
        ["factCheckEmail", "getFollowUpDraft", id],
      ]) {
        for (const m of mutations.findAll({ mutationKey: key })) mutations.remove(m);
      }
    }
    if (kind === "all" || kind === "first") {
      queryClient.removeQueries({ queryKey: ["getFirstDraft", id] });
      for (const m of mutations.findAll({ mutationKey: ["saveFirstDraft", id], status: "error" })) mutations.remove(m);
      for (const m of mutations.findAll({ mutationKey: ["writeLetter", id] })) mutations.remove(m);
      for (const m of mutations.findAll({ mutationKey: ["factCheckEmail", "getFirstDraft", id] })) mutations.remove(m);
    }
  }
}
