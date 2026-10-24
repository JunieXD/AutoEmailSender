import {
  type MutationState,
  keepPreviousData,
  skipToken,
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useEffectEvent, useRef, useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { Material, MentorDetail, ReplyDraft, ReplyDraftEdit, ReplyTarget } from "@/api/types";
import { Attachments } from "@/components/attachments";
import { AttachMenu, LetterProblems, LetterSend, useDraftSave, useUploading } from "@/components/letter-composer";
import { LazyLetterEditor } from "@/components/lazy-letter-editor";
import { usePlaceholders } from "@/components/template-dialog";
import { day, time } from "@/format";
import { letterHtml, letterText } from "@/letter";

/** The reply composer is shared by the mentor page and the conversation page. */
export function ReplyLetter({
  mentor,
  selected,
}: {
  mentor: MentorDetail;
  selected: { id: number; version: number } | null;
}) {
  const draft = useQuery({
    queryKey: ["getReplyDraft", mentor.id],
    queryFn: () => call("getReplyDraft", { id: mentor.id }),
  });
  const latest = useQuery({
    queryKey: ["getReplyTarget", mentor.id],
    queryFn: () => call("getReplyTarget", { mentorId: mentor.id, id: null }),
  });
  const materials = useQuery({ queryKey: ["listMaterials"], queryFn: () => call("listMaterials", {}) });
  const placeholders = usePlaceholders();
  const error = draft.error ?? latest.error ?? materials.error ?? placeholders.error;
  if (error) return <p className="px-5 py-4 text-sm text-destructive">回复草稿未能加载：{errorMessage(error)}</p>;
  return (
    draft.isSuccess &&
    latest.isSuccess &&
    materials.data &&
    placeholders.data && (
      <Composer
        key={mentor.id}
        mentor={mentor}
        draft={draft.data}
        latest={latest.data}
        selected={selected}
        materials={materials.data}
        placeholders={placeholders.data}
      />
    )
  );
}

function Composer({
  mentor,
  draft,
  latest,
  selected,
  materials,
  placeholders,
}: {
  mentor: MentorDetail;
  draft: ReplyDraft | null;
  latest: ReplyTarget | null;
  selected: { id: number; version: number } | null;
  materials: Material[];
  placeholders: string[];
}) {
  const mentorId = mentor.id;
  const queryClient = useQueryClient();
  const { save, last, scope } = useDraftSave<{ letter: ReplyDraftEdit; target: ReplyTarget | null }, ReplyDraft | null>(
    "saveReplyDraft",
    "getReplyDraft",
    mentorId,
    ({ letter }) => call("saveReplyDraft", letter),
  );
  const restored = last?.status !== "success" ? last?.variables : undefined;
  // Saved content comes from the query; only an unfinished save overrides it.
  const [editing, setEditing] = useState(restored);
  const [choice, setChoice] = useState<ReplyTarget | null>(null);
  if (editing && last?.status === "success" && last.variables === editing) {
    setEditing(undefined);
    if (last.data) setChoice(null);
  }
  const shownTarget = editing ? editing.target : (choice ?? (draft ? draft.target : latest));
  const letter: ReplyDraftEdit = editing?.letter ?? {
    mentorId,
    replyTo: shownTarget?.messageId ?? draft?.replyTo ?? "",
    bodyMd: draft?.bodyMd ?? "",
    attachments: draft?.attachments ?? [],
  };
  const [body, setBody] = useState({ version: 0, markdown: letter.bodyMd });
  // A server change (approval, withdrawal or reload) replaces the editor, while typing keeps it.
  if (body.markdown !== letter.bodyMd) setBody({ version: body.version + 1, markdown: letter.bodyMd });
  const approveKey = ["approveReply", mentorId];
  const approvals = useMutationState<MutationState<void, Error, ReplyDraftEdit>>({
    filters: { mutationKey: approveKey },
    select: (m) => m.state,
  });
  const approving = approvals.some((m) => m.status === "pending");
  const uploading = useUploading(mentorId);
  const locked = approving || uploading;
  const chosen = useQuery({
    queryKey: ["getReplyTarget", mentorId, selected?.id],
    queryFn: selected ? () => call("getReplyTarget", { mentorId, id: selected.id }) : skipToken,
  });
  const [selection, setSelection] = useState(0);
  if (!locked && selected && chosen.isSuccess && selection !== selected.version) {
    setSelection(selected.version);
    if (chosen.data?.messageId) {
      setChoice(chosen.data);
      setEditing({ letter: { ...letter, replyTo: chosen.data.messageId }, target: chosen.data });
    }
  }
  const persistTarget = useEffectEvent(() => {
    if (editing?.target?.messageId) save.mutate(editing);
  });
  useEffect(() => {
    if (selection > 0) persistTarget();
  }, [selection]);
  const style = useQuery({ queryKey: ["getLetterStyle"], queryFn: () => call("getLetterStyle", {}) });
  const checks = useQuery({
    queryKey: ["checkReplyDraft", letter, mentor.name, mentor.aliases],
    queryFn: () => call("checkReplyDraft", letter),
    placeholderData: keepPreviousData,
    gcTime: 0,
  });
  const address = mentor.emails.at(0);
  const domain = useQuery({
    queryKey: ["checkMailDomain", address],
    queryFn: address ? () => call("checkMailDomain", { address }) : skipToken,
    staleTime: Infinity,
    refetchOnReconnect: true,
  });
  const problems = [...(checks.data ?? []), ...(domain.data ? [domain.data] : [])];
  const attached = materials.filter((m) => letter.attachments.includes(m.id));
  const approve = useMutation({
    mutationKey: approveKey,
    scope,
    mutationFn: async (current: ReplyDraftEdit) => {
      const saved = queryClient.getQueryData<ReplyDraft | null>(["getReplyDraft", mentorId]);
      if (!saved || saved.replyTo !== current.replyTo || (saved.bodyMd ?? "") !== current.bodyMd)
        throw new Error("回复尚未保存成功。请修改正文重试保存后再发送。");
      if (!style.data) throw new Error("信件样式尚未加载，请稍后再试。");
      await call("approveReply", {
        mentorId,
        draftId: saved.id,
        replyTo: current.replyTo,
        bodyMd: current.bodyMd,
        attachments: current.attachments,
        html: letterHtml(current.bodyMd, style.data),
        text: letterText(current.bodyMd),
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["getReplyDraft", mentorId] });
      await queryClient.invalidateQueries({ queryKey: ["listCorrespondence", mentorId] });
    },
  });
  function edit(next: ReplyDraftEdit) {
    const edited = { letter: next, target: shownTarget };
    setEditing(edited);
    setBody({ ...body, markdown: next.bodyMd });
    save.mutate(edited);
  }
  function rewrite(markdown: string) {
    edit({ ...letter, bodyMd: markdown });
    setBody({ version: body.version + 1, markdown });
  }
  const blocker = !address
    ? "导师尚未填写邮箱，可在资料里填写。"
    : !shownTarget
      ? "回的那封信已不在往来里。请点另一封回信旁的“回复”。"
      : !shownTarget.messageId
        ? "这封来信没有 Message-ID，无法在原线程回复。请到邮箱里回复。"
        : null;
  const approveError = approvals.at(-1)?.error;
  const sendButton = useRef<HTMLButtonElement>(null);
  return (
    <section
      aria-label="回复导师"
      className="mt-auto flex max-h-[65dvh] shrink-0 flex-col gap-2 overflow-y-auto border-t border-border bg-background px-5 py-3 phone:px-3 [&>*]:shrink-0"
      onKeyDown={(e) => {
        if (
          e.key === "Enter" &&
          (e.metaKey || e.ctrlKey) &&
          !e.nativeEvent.isComposing &&
          e.target instanceof HTMLElement &&
          e.currentTarget.contains(e.target)
        ) {
          e.preventDefault();
          sendButton.current?.click();
        }
      }}
    >
      <div className="min-w-0">
        <p className="truncate text-sm font-medium" title={shownTarget?.subject ?? undefined}>
          回复 {mentor.name}：{shownTarget?.subject ?? "（无主题）"}
        </p>
        {shownTarget && (
          <p className="truncate text-xs text-muted-foreground">
            {shownTarget.fromAddress} · {day(shownTarget.sentAt)} {time(shownTarget.sentAt)}
          </p>
        )}
      </div>
      <LazyLetterEditor
        key={body.version}
        id={`reply-letter-${mentorId}`}
        markdown={body.markdown}
        placeholders={placeholders}
        unfilled
        compact
        focus={selection}
        disabled={locked}
        onChange={(bodyMd) => edit({ ...letter, bodyMd })}
      />
      <Attachments
        files={attached}
        onRemove={
          locked ? undefined : (m) => edit({ ...letter, attachments: letter.attachments.filter((id) => id !== m.id) })
        }
      />
      <LetterProblems
        problems={problems}
        locked={locked}
        onSalutation={(salutation) => rewrite(`${salutation}\n\n${letter.bodyMd}`)}
      />
      <div className="flex flex-wrap items-center gap-2">
        {/* Future AI actions belong beside the attachment action in this same composer. */}
        <AttachMenu
          materials={materials}
          attached={attached}
          disabled={locked}
          mentorId={mentorId}
          kind="reply"
          onPrepare={() => {
            const edited = { letter, target: shownTarget };
            setEditing(edited);
            return save.mutateAsync(edited);
          }}
          onAttach={(m) =>
            edit({
              ...letter,
              attachments: [
                ...letter.attachments.filter((id) => !attached.some((a) => a.id === id && a.fileName === m.fileName)),
                m.id,
              ],
            })
          }
        />
        <div className="ml-auto flex min-w-0 items-center gap-3">
          <LetterSend
            buttonRef={sendButton}
            problems={problems}
            blocker={blocker}
            disabled={locked || !style.data || letter.bodyMd.trim() === "" || chosen.isFetching}
            onSend={() => approve.mutate({ ...letter, attachments: attached.map((m) => m.id) })}
          />
        </div>
      </div>
      {last?.status === "error" && <p className="text-sm text-destructive">草稿未能保存：{errorMessage(last.error)}</p>}
      {checks.error && <p className="text-sm text-destructive">发送前检查未能完成：{errorMessage(checks.error)}</p>}
      {(chosen.error ?? style.error ?? approveError) && (
        <p className="text-sm text-destructive">
          未能发送：{errorMessage(chosen.error ?? style.error ?? approveError)}
        </p>
      )}
    </section>
  );
}
