import { type MutationState, useMutation, useMutationState, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { FileText } from "lucide-react";
import { useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { FirstDraft, FirstDraftEdit, TemplateSummary } from "@/api/types";
import { LazyLetterEditor } from "@/components/lazy-letter-editor";
import { usePlaceholders } from "@/components/template-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

/** Writing the first email to a mentor on their page (docs/ui.md#交互). */
export function FirstLetter({ mentorId }: { mentorId: number }) {
  const draft = useQuery({
    queryKey: ["getFirstDraft", mentorId],
    queryFn: () => call("getFirstDraft", { id: mentorId }),
  });
  const placeholders = usePlaceholders();
  const error = draft.error ?? placeholders.error;
  if (error) return <p className="px-5 py-4 text-sm text-destructive">草稿没能加载：{errorMessage(error)}</p>;
  return (
    draft.isSuccess &&
    placeholders.data && (
      <Composer key={mentorId} mentorId={mentorId} draft={draft.data} placeholders={placeholders.data} />
    )
  );
}

/**
 * Saves as the user types. Saves run one at a time and outlive this page, so the last text wins
 * even after leaving and coming back quickly, and unsaved text is shown again on return.
 */
function Composer({
  mentorId,
  draft,
  placeholders,
}: {
  mentorId: number;
  draft: FirstDraft | null;
  placeholders: string[];
}) {
  const queryClient = useQueryClient();
  const mutationKey = ["saveFirstDraft", mentorId];
  const save = useMutation({
    mutationKey,
    scope: { id: `saveFirstDraft ${mentorId}` },
    mutationFn: (d: FirstDraftEdit) => call("saveFirstDraft", d),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["getFirstDraft", mentorId] }),
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
  function edit(next: FirstDraftEdit) {
    setLetter(next);
    save.mutate(next);
  }
  return (
    <div className="flex flex-col gap-2 border-t border-border px-5 py-4 phone:px-3">
      <Input
        aria-label="主题"
        placeholder="主题"
        autoComplete="off"
        value={letter.subject}
        onChange={(e) => edit({ ...letter, subject: e.target.value })}
      />
      <LazyLetterEditor
        key={body.version}
        id={`first-letter-${mentorId}`}
        markdown={body.markdown}
        placeholders={placeholders}
        disabled={false}
        onChange={(bodyMd) => edit({ ...letter, bodyMd })}
      />
      <div className="flex flex-wrap items-center gap-2">
        <TemplateMenu
          mentorId={mentorId}
          written={letter.subject.trim() !== "" || letter.bodyMd.trim() !== ""}
          onApply={(filled) => {
            edit({ mentorId, ...filled });
            setBody({ version: body.version + 1, markdown: filled.bodyMd });
          }}
        />
        {last?.status === "error" && (
          <p className="text-sm text-destructive">草稿未能保存：{errorMessage(last.error)}</p>
        )}
      </div>
    </div>
  );
}

/** Lists the templates; a letter already written is replaced only once confirmed. */
function TemplateMenu({
  mentorId,
  written,
  onApply,
}: {
  mentorId: number;
  written: boolean;
  onApply: (filled: { subject: string; bodyMd: string }) => void;
}) {
  const [open, setOpen] = useState(false);
  const [chosen, setChosen] = useState<TemplateSummary | null>(null);
  const templates = useQuery({ queryKey: ["listTemplates"], queryFn: () => call("listTemplates", {}), enabled: open });
  const apply = useMutation({
    mutationFn: (t: TemplateSummary) => call("applyTemplate", { templateId: t.id, mentorId }),
    onSuccess: (filled) => {
      setOpen(false);
      onApply(filled);
    },
  });
  function choose(t: TemplateSummary) {
    if (written) setChosen(t);
    else apply.mutate(t);
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
      <PopoverTrigger render={<Button type="button" variant="ghost" className="px-2.5" />}>
        <FileText strokeWidth={1.8} />
        使用模板
      </PopoverTrigger>
      <PopoverContent align="start" className="flex w-72 flex-col p-1.5">
        {chosen ? (
          <div className="p-1.5">
            <p>用“{chosen.name}”替换现在写的主题和正文？</p>
            <div className="mt-3 flex justify-end gap-2">
              <Button variant="quiet" onClick={() => setChosen(null)}>
                取消
              </Button>
              <Button disabled={apply.isPending} onClick={() => apply.mutate(chosen)}>
                替换
              </Button>
            </div>
          </div>
        ) : templates.data?.length === 0 ? (
          <p className="p-1.5 text-sm text-muted-foreground">
            还没有模板，可以在
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
