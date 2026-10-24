import { type MutationState, useMutation, useMutationState, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Paperclip, Send, TriangleAlert } from "lucide-react";
import { type ReactNode, type Ref, useEffect, useEffectEvent, useRef } from "react";
import { call, errorMessage } from "@/api/call";
import type { DraftKind, FirstDraft, Material, Problem, ReplyDraft } from "@/api/types";
import { Confirm } from "@/components/batch-bar";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { fileSize } from "@/format";
import { uploadMaterial } from "@/material";

/** Saves outlive the composer and run in order; approval shares their scope and waits for them. */
export function useDraftSave<Edit, Saved>(
  operation: string,
  query: string,
  mentorId: number,
  persist: (edit: Edit) => Promise<Saved>,
) {
  const queryClient = useQueryClient();
  const mutationKey = [operation, mentorId];
  const scope = { id: `${operation} ${mentorId}` };
  const save = useMutation({
    mutationKey,
    scope,
    mutationFn: persist,
    onSuccess: async (saved) => {
      await queryClient.cancelQueries({ queryKey: [query, mentorId] });
      queryClient.setQueryData([query, mentorId], saved);
    },
  });
  const last = useMutationState<MutationState<Saved, Error, Edit>>({
    filters: { mutationKey },
    select: (m) => m.state,
  }).at(-1);
  return { save, last, scope };
}

/** Uploads belong to the mentor, so a remounted editor stays locked until attachment is saved. */
export function useUploading(mentorId: number) {
  return (
    useMutationState({
      filters: { mutationKey: ["uploadLetterMaterial", mentorId], status: "pending" },
    }).length > 0
  );
}

export function LetterProblems({
  problems,
  locked,
  onSalutation,
}: {
  problems: Problem[];
  locked: boolean;
  onSalutation: (salutation: string) => void;
}) {
  return (
    problems.length > 0 && (
      <ul className="flex flex-col gap-2 text-sm text-warning">
        {problems.map((p) => (
          <li key={problemText(p)} className="flex items-start gap-2">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" strokeWidth={1.8} />
            {problemText(p)}
            {p.kind === "no_salutation" && (
              <Button
                variant="quiet"
                size="sm"
                className="-my-1 shrink-0"
                disabled={locked}
                onClick={() => onSalutation(p.salutation)}
              >
                加上称呼
              </Button>
            )}
          </li>
        ))}
      </ul>
    )
  );
}

/** Both clicking and the reply shortcut use the same disabled button and problem confirmation. */
export function LetterSend({
  blocker,
  problems,
  disabled,
  onSend,
  buttonRef,
}: {
  blocker: ReactNode;
  problems: Problem[];
  disabled: boolean;
  onSend: () => void;
  buttonRef?: Ref<HTMLButtonElement>;
}) {
  const button = (
    <Button ref={buttonRef} disabled={disabled || !!blocker} onClick={problems.length === 0 ? onSend : undefined}>
      <Send strokeWidth={1.8} />
      发送
    </Button>
  );
  return (
    <>
      {blocker && <p className="min-w-0 text-sm text-muted-foreground">{blocker}</p>}
      {problems.length > 0 ? (
        <Confirm
          trigger={button}
          message={`还有 ${problems.length} 个问题，仍要发送？`}
          action="仍要发送"
          onConfirm={onSend}
        />
      ) : (
        button
      )}
    </>
  );
}

function problemText(p: Problem): string {
  if (p.kind === "placeholder") return `“{${p.name}}”没有替换，请改成具体内容或删掉。`;
  if (p.kind === "no_salutation") return "正文里没有称呼导师。";
  if (p.kind === "no_attachment") return "正文提到了附件，但还没有添加附件。";
  return `查不到 ${p.domain} 的收信服务器，请核对导师的邮箱地址。`;
}

/** Existing materials and an immediate upload share the same attachment picker. */
export function AttachMenu({
  materials,
  attached,
  disabled,
  onAttach,
  mentorId,
  kind,
  onPrepare,
  onUploaded,
}: {
  materials: Material[];
  attached: Material[];
  disabled: boolean;
  onAttach: (material: Material) => void;
  mentorId: number;
  kind: DraftKind;
  onPrepare: () => Promise<FirstDraft | ReplyDraft | null>;
  onUploaded?: () => void;
}) {
  const queryClient = useQueryClient();
  const picker = useRef<HTMLInputElement>(null);
  const mutationKey = ["uploadLetterMaterial", mentorId, kind];
  const add = useMutation({
    mutationKey,
    mutationFn: async (file: File) => {
      // Save an untouched template before uploading; completion never closes over its body.
      const prepared = await onPrepare();
      const { id } = await uploadMaterial(file);
      await queryClient.invalidateQueries({ queryKey: ["listMaterials"], refetchType: "all" });
      const updated = queryClient.getQueryData<Material[]>(["listMaterials"]);
      const material = updated?.find((m) => m.id === id);
      if (!material) throw new Error("材料已添加，但列表未能刷新。请重新打开附件菜单后选择它。");
      const applied = await call("attachDraftMaterial", { mentorId, kind, materialId: id });
      await queryClient.invalidateQueries({
        queryKey: [kind === "first" ? "getFirstDraft" : "getReplyDraft", mentorId],
        refetchType: "all",
      });
      if (!applied && prepared) throw new Error("草稿或材料已发生变化。请重新打开草稿，从附件菜单选择材料。");
      return { material, applied };
    },
  });
  const last = useMutationState<MutationState<{ material: Material; applied: boolean }, Error, File>>({
    filters: { mutationKey },
    select: (m) => m.state,
  }).at(-1);
  // An empty letter has no saved draft. Only the active picker attaches to that local input.
  const complete = useEffectEvent(() => {
    if (last?.status !== "success" || !last.data) return;
    if (last.data.applied) onUploaded?.();
    else onAttach(last.data.material);
    const cache = queryClient.getMutationCache();
    for (const mutation of cache.findAll({ mutationKey, status: "success" })) cache.remove(mutation);
  });
  useEffect(() => {
    if (last?.status === "success") complete();
  }, [last]);
  return (
    <Popover>
      <PopoverTrigger render={<Button type="button" variant="ghost" className="px-2.5" disabled={disabled} />}>
        <Paperclip strokeWidth={1.8} />
        {last?.status === "pending" ? "正在添加附件…" : "添加附件"}
      </PopoverTrigger>
      <PopoverContent align="start" className="flex max-h-[70dvh] w-72 flex-col overflow-y-auto p-1.5">
        {materials.length === 0 ? (
          <p className="p-1.5 text-sm text-muted-foreground">
            尚未添加材料，可当场添加，或在
            <Link to="/settings/materials" className="text-brand-text">
              设置 › 个人 › 材料
            </Link>
            里添加。
          </p>
        ) : (
          materials.map((m) => {
            const taken = attached.find((a) => a.fileName === m.fileName);
            return (
              <button
                key={m.id}
                type="button"
                disabled={taken !== undefined || disabled}
                className="flex flex-col rounded-md px-3 py-2 text-left enabled:hover:bg-muted disabled:opacity-40"
                onClick={() => onAttach(m)}
              >
                <span className="truncate" title={m.fileName}>
                  {m.fileName}
                </span>
                <span className="text-sm text-muted-foreground">
                  {taken === m ? "已附上" : taken ? "已附上同名文件" : fileSize(m.size)}
                </span>
              </button>
            );
          })
        )}
        <input
          ref={picker}
          type="file"
          accept=".pdf,.docx,.doc"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (file) add.mutate(file);
          }}
        />
        <Button variant="ghost" disabled={disabled} onClick={() => picker.current?.click()}>
          {last?.status === "pending" ? "正在提取文字…" : "添加新材料"}
        </Button>
        {last?.error && <p className="p-1.5 text-sm text-destructive">{errorMessage(last.error)}</p>}
      </PopoverContent>
    </Popover>
  );
}
