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
import type { Material, MentorDetail, ReplyDraft, ReplyDraftEdit, ReplyTarget, FollowUpDraft } from "@/api/types";
import {
  AiButton,
  FactCheckStatus,
  Rewrite,
  useFactCheck,
  useReplyWritingTarget,
  useWriting,
} from "@/components/ai-writing";
import { TemplateMenu } from "@/components/first-letter";
import { Confirm } from "@/components/batch-bar";
import { Button } from "@/components/ui/button";
import { Attachments } from "@/components/attachments";
import {
  AttachMenu,
  LetterProblems,
  LetterSend,
  quotes,
  useDraftSave,
  useUploading,
} from "@/components/letter-composer";
import { LazyLetterEditor } from "@/components/lazy-letter-editor";
import { usePlaceholders } from "@/components/template-dialog";
import { composing } from "@/composing";
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
  if (draft.isSuccess && latest.isSuccess && !draft.data && !latest.data) {
    return (
      <p className="mt-auto border-t border-border px-5 py-3 text-sm text-muted-foreground phone:px-3">
        这些信缺少邮件编号，无法在原线程里回复，请到邮箱里回复
      </p>
    );
  }
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

/** Follow-ups keep a fixed outgoing target while sharing the reply editor and save lifecycle. */
export function FollowUpLetter({ mentor }: { mentor: MentorDetail }) {
  const draft = useQuery({
    queryKey: ["getFollowUpDraft", mentor.id],
    queryFn: () => call("getFollowUpDraft", { id: mentor.id }),
  });
  const materials = useQuery({ queryKey: ["listMaterials"], queryFn: () => call("listMaterials", {}) });
  const placeholders = usePlaceholders();
  const error = draft.error ?? materials.error ?? placeholders.error;
  if (error) return <p className="px-5 py-4 text-sm text-destructive">追问草稿未能加载：{errorMessage(error)}</p>;
  return (
    draft.data &&
    materials.data &&
    placeholders.data && (
      <Composer
        key={draft.data.id}
        mentor={mentor}
        draft={draft.data}
        latest={draft.data.target}
        selected={null}
        materials={materials.data}
        placeholders={placeholders.data}
        followUpId={draft.data.id}
      />
    )
  );
}

type ThreadTarget = Omit<ReplyTarget, "id">;

function Composer({
  mentor,
  draft,
  latest,
  selected,
  materials,
  placeholders,
  followUpId = null,
}: {
  mentor: MentorDetail;
  draft: ReplyDraft | FollowUpDraft | null;
  latest: ThreadTarget | null;
  selected: { id: number; version: number } | null;
  materials: Material[];
  placeholders: string[];
  followUpId?: number | null;
}) {
  const mentorId = mentor.id;
  const followUp = followUpId !== null;
  const draftQuery = followUp ? "getFollowUpDraft" : "getReplyDraft";
  const label = followUp ? "追问" : "回复";
  const queryClient = useQueryClient();
  const { save, last, scope, flush } = useDraftSave<
    { letter: ReplyDraftEdit; target: ThreadTarget | null },
    ReplyDraft | FollowUpDraft | null
  >(
    followUp ? "saveFollowUpDraft" : "saveReplyDraft",
    draftQuery,
    mentorId,
    ({ letter }) =>
      followUpId !== null
        ? call("saveFollowUpDraft", { ...letter, draftId: followUpId })
        : call("saveReplyDraft", letter),
    followUpId ?? undefined,
  );
  const restored = last?.status !== "success" ? last?.variables : undefined;
  // Saved content comes from the query; only an unfinished save overrides it.
  const [editing, setEditing] = useState(restored);
  const [choice, setChoice] = useState<ThreadTarget | null>(null);
  const aiTarget = useReplyWritingTarget(mentorId);
  const shownTarget = editing ? editing.target : (choice ?? (draft ? draft.target : (aiTarget ?? latest)));
  const letter: ReplyDraftEdit = editing?.letter ?? {
    mentorId,
    replyTo: shownTarget?.messageId ?? draft?.replyTo ?? "",
    bodyMd: draft?.bodyMd ?? "",
    attachments: draft?.attachments ?? [],
  };
  const [body, setBody] = useState({ version: 0, markdown: letter.bodyMd });
  if (editing && last?.status === "success" && last.variables === editing) {
    setEditing(undefined);
    if (last.data) setChoice(null);
    // Saving may normalize whitespace; it must not replace the editor the user is typing in.
    setBody({ ...body, markdown: last.data?.bodyMd ?? "" });
  } else if (body.markdown !== letter.bodyMd) {
    // Approval, withdrawal or a reload replaces the content rather than acknowledging a save.
    setBody({ version: body.version + 1, markdown: letter.bodyMd });
  }
  const approveKey = followUpId !== null ? ["approveFollowUp", mentorId, followUpId] : ["approveReply", mentorId];
  const approvals = useMutationState<MutationState<void, Error, ReplyDraftEdit>>({
    filters: { mutationKey: approveKey },
    select: (m) => m.state,
  });
  const approving = approvals.some((m) => m.status === "pending");
  const discard = useMutation({
    scope,
    mutationFn: () => (followUpId !== null ? call("discardFollowUpDraft", { id: followUpId }) : Promise.resolve()),
    onSuccess: async () => {
      writing.forget();
      factCheck.forget();
      await queryClient.invalidateQueries({ queryKey: [draftQuery, mentorId] });
      await queryClient.invalidateQueries({ queryKey: ["listCorrespondence", mentorId] });
    },
  });
  const uploading = useUploading(mentorId);
  const chosen = useQuery({
    queryKey: ["getReplyTarget", mentorId, selected?.id],
    queryFn: selected ? () => call("getReplyTarget", { mentorId, id: selected.id }) : skipToken,
  });
  const [selection, setSelection] = useState({ version: 0, previousReplyTo: "" });
  const selecting = selected !== null && selection.version !== selected.version;
  const writing = useWriting(mentorId, undefined, followUpId ?? undefined, followUp ? undefined : letter.replyTo);
  const answer = !selecting && writing.latest?.status === "success" ? writing.latest.data : undefined;
  const busy = writing.latest?.status === "pending";
  const fills = answer !== undefined && letter.bodyMd.trim() === "";
  const review =
    answer && !fills
      ? { before: { subject: "", bodyMd: letter.bodyMd }, after: { ...answer, subject: "" } }
      : undefined;
  const factCheck = useFactCheck([draftQuery, mentorId], followUp ? "checkFollowUpDraft" : "checkReplyDraft", flush);
  const fill = useEffectEvent(() => {
    if (answer) {
      writing.forget();
      rewrite(answer.bodyMd);
      factCheck.start();
    }
  });
  useEffect(() => {
    if (answer && fills) fill();
  }, [answer, fills]);
  const locked = approving || discard.isPending || uploading || busy || review !== undefined;
  const canSelect = !approving && !discard.isPending && !uploading;
  if (canSelect && selecting && selected && chosen.data?.messageId) {
    setSelection({ version: selected.version, previousReplyTo: letter.replyTo });
    setChoice(chosen.data);
    setEditing({ letter: { ...letter, replyTo: chosen.data.messageId }, target: chosen.data });
  }
  const persistTarget = useEffectEvent(() => {
    if (!editing?.target?.messageId) return;
    if (selection.previousReplyTo !== editing.letter.replyTo) {
      const cache = queryClient.getMutationCache();
      for (const m of cache.findAll({ mutationKey: ["writeReply", mentorId, selection.previousReplyTo] }))
        cache.remove(m);
    }
    save.mutate(editing);
  });
  useEffect(() => {
    if (selection.version > 0) persistTarget();
  }, [selection]);
  const style = useQuery({ queryKey: ["getLetterStyle"], queryFn: () => call("getLetterStyle", {}) });
  const checks = useQuery({
    queryKey: [followUp ? "checkFollowUpDraft" : "checkReplyDraft", letter, mentor.name, mentor.aliases],
    queryFn: () => call(followUp ? "checkFollowUpDraft" : "checkReplyDraft", letter),
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
      const saved = queryClient.getQueryData<ReplyDraft | FollowUpDraft | null>([draftQuery, mentorId]);
      if (!saved || saved.replyTo !== current.replyTo || (saved.bodyMd ?? "") !== current.bodyMd)
        throw new Error(`${label}尚未保存成功。请修改正文重试保存后再发送。`);
      if (!style.data) throw new Error("信件样式尚未加载，请稍后再试。");
      await call(followUp ? "approveFollowUp" : "approveReply", {
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
      await queryClient.invalidateQueries({ queryKey: [draftQuery, mentorId] });
      writing.forget();
      factCheck.forget();
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
      ? followUp
        ? "上一封已不在往来里，请放弃这份追问后重新选择。"
        : "回的那封信已不在往来里。请点另一封回信旁的“回复”。"
      : !shownTarget.messageId
        ? "这封来信没有 Message-ID，无法在原线程回复。请到邮箱里回复。"
        : null;
  const approveError = approvals.at(-1)?.error;
  const sendButton = useRef<HTMLButtonElement>(null);
  return (
    <section
      aria-label={`${label}导师`}
      className="mt-auto flex max-h-[65dvh] shrink-0 flex-col gap-2 overflow-y-auto border-t border-border bg-background px-5 py-3 phone:px-3 [&>*]:shrink-0"
      onKeyDown={(e) => {
        if (
          e.key === "Enter" &&
          (e.metaKey || e.ctrlKey) &&
          !composing(e) &&
          e.target instanceof HTMLElement &&
          e.currentTarget.contains(e.target)
        ) {
          e.preventDefault();
          sendButton.current?.click();
        }
      }}
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 grow">
          <p className="truncate text-sm font-medium">
            {label} {mentor.name}
          </p>
          {shownTarget && (
            <p className="truncate text-xs text-muted-foreground" title={shownTarget.subject ?? undefined}>
              “{shownTarget.subject ?? "无主题"}” · {shownTarget.fromAddress} · {day(shownTarget.sentAt)}{" "}
              {time(shownTarget.sentAt)}
            </p>
          )}
        </div>
        {followUp && (
          <Confirm
            trigger={
              <Button variant="ghost" size="sm" disabled={locked}>
                放弃追问
              </Button>
            }
            message="放弃这份追问草稿？已保存的正文和取消原因会保留在往来里。"
            action="放弃追问"
            onConfirm={() => discard.mutate()}
          />
        )}
      </div>
      {followUp && (
        <p className="text-xs text-muted-foreground">主题自动沿用上一封。AI 起草会将之前发出的信交给所选模型参考。</p>
      )}
      {review && <Rewrite {...review} />}
      <LazyLetterEditor
        key={body.version}
        id={`reply-letter-${mentorId}`}
        markdown={body.markdown}
        placeholders={placeholders}
        unfilled
        compact
        quotes={quotes(problems)}
        focus={selection.version}
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
      {!review && <FactCheckStatus check={factCheck} stored={draft?.factCheck} />}
      <div className="flex flex-wrap items-center gap-2">
        {busy ? (
          <>
            <Button variant="quiet" onClick={writing.forget}>
              取消
            </Button>
            <span className="text-sm text-muted-foreground">
              AI 正在{writing.latest?.variables?.bodyMd.trim() ? "改写" : "起草"}…
            </span>
          </>
        ) : (
          <AiButton
            blank={!letter.bodyMd.trim()}
            disabled={locked || !shownTarget?.messageId || chosen.isFetching}
            onWrite={() => writing.start({ ...letter, subject: "", replyTarget: shownTarget })}
          />
        )}
        {followUp && (
          <TemplateMenu
            mentorId={mentorId}
            disabled={locked}
            written={!!letter.bodyMd.trim()}
            bodyOnly
            onApply={(filled) => rewrite(filled.bodyMd)}
          />
        )}
        <AttachMenu
          materials={materials}
          attached={attached}
          disabled={locked}
          mentorId={mentorId}
          kind={followUp ? "follow_up" : "reply"}
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
          {review ? (
            <>
              <Button variant="quiet" onClick={writing.forget}>
                保留原文
              </Button>
              <Button
                onClick={() => {
                  writing.forget();
                  rewrite(review.after.bodyMd);
                  factCheck.start();
                }}
              >
                采用修改
              </Button>
            </>
          ) : (
            <LetterSend
              buttonRef={sendButton}
              problems={problems}
              blocker={blocker}
              disabled={locked || !style.data || letter.bodyMd.trim() === "" || chosen.isFetching}
              onSend={() => approve.mutate({ ...letter, attachments: attached.map((m) => m.id) })}
            />
          )}
        </div>
      </div>
      {discard.error && <p className="text-sm text-destructive">未能放弃追问：{errorMessage(discard.error)}</p>}
      {writing.latest?.error && (
        <p className="text-sm text-destructive">AI 撰写未能完成：{errorMessage(writing.latest.error)}</p>
      )}
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
