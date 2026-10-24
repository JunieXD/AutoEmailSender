import { type MutationState, useMutation, useMutationState, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { FileText, Send } from "lucide-react";
import { useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { FirstDraft, FirstDraftEdit, FirstEmail, MentorDetail, TemplateSummary } from "@/api/types";
import { LazyLetterEditor } from "@/components/lazy-letter-editor";
import { dropDrafts } from "@/components/mentor";
import { usePlaceholders } from "@/components/template-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { letterHtml, letterText } from "@/letter";

/** Writing the first email to a mentor on their page (docs/ui.md#交互). */
export function FirstLetter({ mentor }: { mentor: MentorDetail }) {
  const mentorId = mentor.id;
  const draft = useQuery({
    queryKey: ["getFirstDraft", mentorId],
    queryFn: () => call("getFirstDraft", { id: mentorId }),
  });
  const placeholders = usePlaceholders();
  const error = draft.error ?? placeholders.error;
  if (error) return <p className="px-5 py-4 text-sm text-destructive">草稿未能加载：{errorMessage(error)}</p>;
  return (
    draft.isSuccess &&
    placeholders.data && <Composer key={mentorId} mentor={mentor} draft={draft.data} placeholders={placeholders.data} />
  );
}

/**
 * Saves as the user types. Saves run one at a time and outlive this page, so the last text wins
 * even after leaving and coming back quickly, and unsaved text is shown again on return.
 */
function Composer({
  mentor,
  draft,
  placeholders,
}: {
  mentor: MentorDetail;
  draft: FirstDraft | null;
  placeholders: string[];
}) {
  const mentorId = mentor.id;
  const queryClient = useQueryClient();
  const mutationKey = ["saveFirstDraft", mentorId];
  // Approving waits for the saves before it, which share this scope.
  const scope = { id: `saveFirstDraft ${mentorId}` };
  const save = useMutation({
    mutationKey,
    scope,
    mutationFn: (d: FirstDraftEdit) => call("saveFirstDraft", d),
    // A load still under way may have read the draft before this save, so it must not land afterwards.
    onSuccess: async (saved) => {
      await queryClient.cancelQueries({ queryKey: ["getFirstDraft", mentorId] });
      queryClient.setQueryData(["getFirstDraft", mentorId], saved);
    },
  });
  const last = useMutationState<MutationState<null, Error, FirstDraftEdit>>({
    filters: { mutationKey },
    select: (m) => m.state,
  }).at(-1);
  const [letter, setLetter] = useState<FirstDraftEdit>(() =>
    last?.status !== "success" && last?.variables
      ? last.variables
      : { mentorId, subject: draft?.subject ?? "", bodyMd: draft?.bodyMd ?? "" },
  );
  // The editor reads its text when mounted, so a template applied mounts a new one.
  const [body, setBody] = useState({ version: 0, markdown: letter.bodyMd });
  const style = useQuery({ queryKey: ["getLetterStyle"], queryFn: () => call("getLetterStyle", {}) });
  const mailboxes = useQuery({ queryKey: ["listMailboxes"], queryFn: () => call("listMailboxes", {}) });
  const approveKey = ["approveFirstEmail", mentorId];
  const approve = useMutation({
    mutationKey: approveKey,
    scope,
    mutationFn: (e: FirstEmail) => call("approveFirstEmail", e),
    onSuccess: async () => {
      // The correspondence takes this composer's place first, so that it does not show the draft emptied.
      await queryClient.invalidateQueries({ queryKey: ["listEmails", mentorId] });
      dropDrafts(queryClient, [mentorId]);
    },
  });
  // An approval still waiting for the saves before it holds the letter even after leaving and coming
  // back, so that no second email can be written and approved meanwhile.
  const approving = useMutationState({ filters: { mutationKey: approveKey, status: "pending" } }).length > 0;
  const sending = approving || approve.isSuccess;
  function edit(next: FirstDraftEdit) {
    setLetter(next);
    save.mutate(next);
  }
  const blocker = !mentor.email ? (
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
    <div className="flex flex-col gap-2 border-t border-border px-5 py-4 phone:px-3">
      <Input
        aria-label="主题"
        placeholder="主题"
        autoComplete="off"
        value={letter.subject}
        disabled={sending}
        onChange={(e) => edit({ ...letter, subject: e.target.value })}
      />
      <LazyLetterEditor
        key={body.version}
        id={`first-letter-${mentorId}`}
        markdown={body.markdown}
        placeholders={placeholders}
        disabled={sending}
        onChange={(bodyMd) => edit({ ...letter, bodyMd })}
      />
      <div className="flex flex-wrap items-center gap-2">
        <TemplateMenu
          mentorId={mentorId}
          disabled={sending}
          written={letter.subject.trim() !== "" || letter.bodyMd.trim() !== ""}
          onApply={(filled) => {
            edit({ mentorId, ...filled });
            setBody({ version: body.version + 1, markdown: filled.bodyMd });
          }}
        />
        {last?.status === "error" && (
          <p className="text-sm text-destructive">草稿未能保存：{errorMessage(last.error)}</p>
        )}
        <div className="ml-auto flex items-center gap-3">
          {blocker && <p className="text-sm text-muted-foreground">{blocker}</p>}
          <Button
            disabled={
              sending ||
              blocker !== null ||
              !style.data ||
              !mailboxes.data ||
              letter.subject.trim() === "" ||
              letter.bodyMd.trim() === ""
            }
            onClick={() => {
              if (style.data) {
                approve.mutate({
                  ...letter,
                  html: letterHtml(letter.bodyMd, style.data),
                  text: letterText(letter.bodyMd),
                });
              }
            }}
          >
            <Send strokeWidth={1.8} />
            发送
          </Button>
        </div>
      </div>
      {(style.error ?? mailboxes.error ?? approve.error) && (
        <p className="text-sm text-destructive">
          未能发送：{errorMessage(style.error ?? mailboxes.error ?? approve.error)}
        </p>
      )}
    </div>
  );
}

/** Lists the templates; a letter already written is replaced only once confirmed. */
function TemplateMenu({
  mentorId,
  disabled,
  written,
  onApply,
}: {
  mentorId: number;
  disabled: boolean;
  written: boolean;
  onApply: (filled: { subject: string; bodyMd: string }) => void;
}) {
  const [open, setOpen] = useState(false);
  const [chosen, setChosen] = useState<TemplateSummary | null>(null);
  const templates = useQuery({ queryKey: ["listTemplates"], queryFn: () => call("listTemplates", {}), enabled: open });
  const apply = useMutation({
    mutationFn: (t: TemplateSummary) => call("applyTemplate", { templateId: t.id, mentorId }),
  });
  // Closing the menu or canceling resets the mutation, so a template still on its way is not applied.
  function fill(t: TemplateSummary) {
    apply.mutate(t, {
      onSuccess: (filled) => {
        setOpen(false);
        onApply(filled);
      },
    });
  }
  function choose(t: TemplateSummary) {
    if (written) setChosen(t);
    else fill(t);
  }
  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        setChosen(null);
        apply.reset();
      }}
    >
      <PopoverTrigger render={<Button type="button" variant="ghost" className="px-2.5" disabled={disabled} />}>
        <FileText strokeWidth={1.8} />
        使用模板
      </PopoverTrigger>
      <PopoverContent align="start" className="flex w-72 flex-col p-1.5">
        {chosen ? (
          <div className="p-1.5">
            <p>用“{chosen.name}”替换现在写的主题和正文？</p>
            <div className="mt-3 flex justify-end gap-2">
              <Button
                variant="quiet"
                onClick={() => {
                  setChosen(null);
                  apply.reset();
                }}
              >
                取消
              </Button>
              <Button disabled={apply.isPending} onClick={() => fill(chosen)}>
                替换
              </Button>
            </div>
          </div>
        ) : templates.data?.length === 0 ? (
          <p className="p-1.5 text-sm text-muted-foreground">
            尚未添加模板，可在
            <Link to="/settings/templates" className="text-brand-text">
              设置 › 个人 › 模板
            </Link>
            里添加。
          </p>
        ) : (
          templates.data?.map((t) => (
            <button
              key={t.id}
              type="button"
              disabled={apply.isPending}
              className="flex flex-col rounded-md px-3 py-2 text-left hover:bg-muted"
              onClick={() => choose(t)}
            >
              <span className="truncate">{t.name}</span>
              <span className="truncate text-sm text-muted-foreground">{t.subject}</span>
            </button>
          ))
        )}
        {(templates.error ?? apply.error) && (
          <p className="p-1.5 text-sm text-destructive">{errorMessage(templates.error ?? apply.error)}</p>
        )}
      </PopoverContent>
    </Popover>
  );
}
