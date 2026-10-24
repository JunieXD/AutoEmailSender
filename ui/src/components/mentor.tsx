import type { QueryClient } from "@tanstack/react-query";
import type { Affiliation, Contact, ContactStatus, MatchState } from "@/api/types";
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

/** The contact status after its coloured dot, with a red dot for a new reply. */
export function ContactLabel({ contact, className = "" }: { contact: Contact; className?: string }) {
  const [label, dot] = STATUS_STYLE[contact.status];
  return (
    <div className={`flex items-center gap-1.5 ${className}`}>
      <span className={`size-2 shrink-0 rounded-full ${dot}`} />
      {label}
      {contact.status === "replied" && contact.unread && (
        <span className="size-2 shrink-0 rounded-full bg-unread" aria-label="有新回信" />
      )}
    </div>
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

/** Labels and colours for a match analysis that has no score. */
export const MATCH_LABEL: Record<Exclude<MatchState["state"], "done">, [string, string]> = {
  running: ["分析中", "text-muted-foreground"],
  queued: ["排队中", "text-muted-foreground"],
  failed: ["分析失败", "text-warning"],
  not_analyzed: ["未分析", "text-faint"],
};

/**
 * After mentors are trashed, restored or deleted, or new mail came, every view of them is out of
 * date, their correspondence too.
 */
export function refreshMentors(queryClient: QueryClient) {
  for (const queryKey of [
    ["listMentors"],
    ["listTrash"],
    ["getMentor"],
    ["listCorrespondence"],
    ["getReplyTarget"],
    ["getReplyDraft"],
    ["listConversations"],
    ["unreadCount"],
  ]) {
    void queryClient.invalidateQueries({ queryKey });
  }
}

/**
 * Drafts the trash canceled or the user approved are gone, and must not come back from the cache, a
 * failed save or what AI wrote for them when the mentor is restored or the email taken back to a draft.
 */
export function dropDrafts(queryClient: QueryClient, ids: number[], kind: "first" | "all" = "all") {
  const mutations = queryClient.getMutationCache();
  for (const id of ids) {
    if (kind === "all") {
      queryClient.removeQueries({ queryKey: ["getReplyDraft", id] });
      for (const m of mutations.findAll({ mutationKey: ["saveReplyDraft", id], status: "error" })) mutations.remove(m);
    }
    queryClient.removeQueries({ queryKey: ["getFirstDraft", id] });
    for (const m of mutations.findAll({ mutationKey: ["saveFirstDraft", id], status: "error" })) mutations.remove(m);
    for (const m of mutations.findAll({ mutationKey: ["writeLetter", id] })) mutations.remove(m);
  }
}
