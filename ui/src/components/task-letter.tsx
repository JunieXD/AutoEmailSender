import {
  keepPreviousData,
  skipToken,
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useImperativeHandle, useRef, useState, type Ref, type ReactNode } from "react";
import { call, errorMessage } from "@/api/call";
import type { DraftEdit, Material, MentorDetail, TaskDraft, TaskSummary } from "@/api/types";
import { AiButton, Rewrite, useWriting } from "@/components/ai-writing";
import { Attachments } from "@/components/attachments";
import {
  AttachMenu,
  LetterProblems,
  LetterSend,
  useDraftSave,
  useUploading,
  waitForSaves,
  type ComposerHandle,
} from "@/components/letter-composer";
import { LazyLetterEditor } from "@/components/lazy-letter-editor";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { letterHtml, letterText } from "@/letter";

/** Task-specific state uses the same saving, picker, rules and approval controls as first letters and replies. */
export function TaskLetter({
  mentor,
  draft,
  task,
  materials,
  placeholders,
  actions,
  onApproved,
  ref,
}: {
  mentor: MentorDetail;
  draft: TaskDraft;
  task: TaskSummary;
  materials: Material[];
  placeholders: string[];
  actions?: ReactNode;
  onApproved: () => void;
  ref?: Ref<ComposerHandle>;
}) {
  const client = useQueryClient();
  const { save, last, scope, flush } = useDraftSave<DraftEdit, TaskDraft>(
    "saveTaskDraft",
    "getTaskDraft",
    draft.id,
    async (input) => {
      await call("saveTaskDraft", input);
      const stored = await call("getTaskDraft", { taskId: task.id, id: draft.id });
      void client.invalidateQueries({ queryKey: ["listTaskDrafts", task.id] });
      return stored;
    },
  );
  const restored = last?.status !== "success" ? last?.variables : undefined;
  const [letter, setLetter] = useState<DraftEdit>(
    () =>
      restored ?? {
        taskId: task.id,
        id: draft.id,
        subject: draft.subject,
        bodyMd: draft.bodyMd,
        attachments: draft.attachments,
      },
  );
  const [body, setBody] = useState({ version: 0, markdown: letter.bodyMd });
  const attached = materials.filter((m) => letter.attachments.includes(m.id));
  const writing = useWriting(mentor.id, { taskId: task.id, id: draft.id });
  const busy = writing.latest?.status === "pending";
  const review =
    writing.latest?.status === "success" && writing.latest.data
      ? { before: letter, after: writing.latest.data }
      : undefined;
  const readOnly = draft.state !== "review" || task.state === "stopped";
  const uploading = useUploading(mentor.id);
  const approveKey = ["approveTaskEmails", draft.id];
  const approving = useMutationState({ filters: { mutationKey: approveKey, status: "pending" } }).length > 0;
  const style = useQuery({ queryKey: ["getLetterStyle"], queryFn: () => call("getLetterStyle", {}) });
  const mailboxes = useQuery({ queryKey: ["listMailboxes"], queryFn: () => call("listMailboxes", {}) });
  const checks = useQuery({
    queryKey: ["checkTaskDraft", letter, mentor.name, mentor.aliases],
    queryFn: () => call("checkTaskDraft", letter),
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
  async function saved() {
    await waitForSaves(client, ["uploadLetterMaterial", mentor.id], false);
    await flush();
  }
  const approve = useMutation({
    mutationKey: approveKey,
    scope,
    mutationFn: async (current: DraftEdit) => {
      await saved();
      if (!style.data) throw new Error("信件样式尚未加载，请稍后再试。");
      await call("approveTaskEmails", {
        taskId: task.id,
        emails: [
          {
            ...current,
            attachments: attached.map((m) => m.id),
            html: letterHtml(current.bodyMd, style.data),
            text: letterText(current.bodyMd),
          },
        ],
      });
    },
    onSuccess: onApproved,
  });
  const locked = readOnly || busy || uploading || approving || approve.isSuccess || review !== undefined;
  const sendButton = useRef<HTMLButtonElement>(null);
  useImperativeHandle(ref, () => ({ flush: saved, approve: () => sendButton.current?.click() }));
  function edit(next: DraftEdit) {
    setLetter(next);
    save.mutate(next);
  }
  function rewrite(next: DraftEdit) {
    edit(next);
    setBody({ version: body.version + 1, markdown: next.bodyMd });
  }
  const blocker = !address ? (
    "导师尚未填写邮箱，可在资料里填写。"
  ) : mailboxes.data?.length === 0 ? (
    <>
      尚未添加发信的邮箱，可在
      <Link to="/settings/mailboxes" className="text-brand-text">
        设置 › 邮件 › 邮箱
      </Link>
      里添加。
    </>
  ) : null;
  return (
    <div className="flex flex-col gap-3 px-5 py-4 phone:px-3">
      {review && !readOnly && <Rewrite {...review} />}
      <div className={review && !readOnly ? "hidden" : "flex flex-col gap-2"}>
        <Input
          aria-label="主题"
          placeholder="主题"
          value={letter.subject}
          disabled={locked}
          onChange={(e) => edit({ ...letter, subject: e.target.value })}
        />
        <LazyLetterEditor
          key={body.version}
          id={`task-letter-${draft.id}`}
          markdown={body.markdown}
          placeholders={placeholders}
          unfilled
          disabled={locked}
          onChange={(bodyMd) => edit({ ...letter, bodyMd })}
        />
      </div>
      <Attachments
        files={attached}
        onRemove={
          locked ? undefined : (m) => edit({ ...letter, attachments: letter.attachments.filter((id) => id !== m.id) })
        }
      />
      {!readOnly && !review && (
        <LetterProblems
          problems={problems}
          locked={locked}
          onSalutation={(salutation) => rewrite({ ...letter, bodyMd: `${salutation}\n\n${letter.bodyMd}` })}
        />
      )}
      <div className="flex flex-wrap items-center gap-2">
        {!readOnly &&
          (busy ? (
            <>
              <Button variant="quiet" onClick={writing.forget}>
                取消
              </Button>
              <p className="text-sm text-muted-foreground">AI 正在改写…</p>
            </>
          ) : (
            <AiButton
              modelId={task.modelConfigId}
              blank={false}
              disabled={locked}
              onWrite={() => writing.start({ mentorId: mentor.id, ...letter })}
            />
          ))}
        {!readOnly && (
          <AttachMenu
            materials={materials}
            attached={attached}
            disabled={locked}
            mentorId={mentor.id}
            kind="first"
            task={{ taskId: task.id, id: draft.id }}
            onPrepare={() => save.mutateAsync(letter)}
            onUploaded={() => {
              const current = client.getQueryData<TaskDraft>(["getTaskDraft", draft.id]);
              if (current) {
                setLetter({
                  ...letter,
                  subject: current.subject,
                  bodyMd: current.bodyMd,
                  attachments: current.attachments,
                });
                if (current.bodyMd !== letter.bodyMd) setBody({ version: body.version + 1, markdown: current.bodyMd });
              }
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
        )}
        {actions}
        {last?.status === "pending" && <p className="text-xs text-muted-foreground">保存中…</p>}
        {last?.status === "success" && <p className="text-xs text-muted-foreground">已保存</p>}
        {!readOnly && (
          <div className="ml-auto flex min-w-0 items-center gap-3">
            {review ? (
              <>
                <Button variant="quiet" onClick={writing.forget}>
                  保留原文
                </Button>
                <Button
                  onClick={() => {
                    writing.forget();
                    rewrite({ ...letter, ...review.after });
                  }}
                >
                  采用修改
                </Button>
              </>
            ) : (
              <LetterSend
                label="批准并查看下一封"
                buttonRef={sendButton}
                blocker={blocker}
                problems={problems}
                disabled={locked || !style.data || !mailboxes.data || !letter.subject.trim() || !letter.bodyMd.trim()}
                onSend={() => approve.mutate(letter)}
              />
            )}
          </div>
        )}
      </div>
      {last?.status === "error" && (
        <p className="text-sm text-destructive">
          草稿未能保存：{errorMessage(last.error)}
          <Button variant="quiet" size="sm" onClick={() => save.mutate(letter)}>
            重试保存
          </Button>
        </p>
      )}
      {(checks.error ?? writing.latest?.error ?? style.error ?? mailboxes.error ?? approve.error) && (
        <p className="text-sm text-destructive">
          {errorMessage(checks.error ?? writing.latest?.error ?? style.error ?? mailboxes.error ?? approve.error)}
        </p>
      )}
    </div>
  );
}
