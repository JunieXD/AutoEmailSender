import type { ReactNode } from "react";
import {
  type MutationState,
  type QueryClient,
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { TriangleAlert } from "lucide-react";
import { call, errorMessage } from "@/api/call";
import type { FactCheck, FirstDraftEdit, Letter, ModelConfig, ModelProvider, ReplyTarget } from "@/api/types";
import { Confirm } from "@/components/batch-bar";
import { useModelProviders } from "@/components/model-dialog";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { changes } from "@/diff";
import { Changes } from "@/components/changes";
import { waitForSaves } from "@/components/letter-composer";
import { letterText } from "@/letter";

// AI writing in a composer (docs/ui.md#交互).

type WritingEdit = FirstDraftEdit & { replyTarget?: Omit<ReplyTarget, "id"> | null };

/** An empty reply has no saved draft yet; its AI request keeps the selected target on return. */
export function useReplyWritingTarget(mentorId: number) {
  const latest = useMutationState<MutationState<Letter, Error, WritingEdit>>({
    filters: { mutationKey: ["writeReply", mentorId] },
    select: (m) => m.state,
  }).at(-1);
  return latest?.variables?.replyTarget;
}

/**
 * The model writing a mentor's letter. The request and its answer stay in the mutation cache
 * until forgotten, so that leaving the page neither stops it nor loses what comes back.
 */
export function useWriting(
  mentorId: number,
  task?: { taskId: number; id: number },
  followUpId?: number,
  replyTo?: string,
) {
  const queryClient = useQueryClient();
  const mutationKey = task
    ? ["writeTaskDraft", task.id]
    : followUpId !== undefined
      ? ["writeFollowUp", mentorId, followUpId]
      : replyTo !== undefined
        ? ["writeReply", mentorId, replyTo]
        : ["writeLetter", mentorId];
  const write = useMutation({
    mutationKey,
    mutationFn: async (letter: WritingEdit) => {
      if (task) return call("writeTaskDraft", { ...letter, ...task });
      if (replyTo !== undefined) {
        await waitForSaves(queryClient, ["saveReplyDraft", mentorId]);
        return call("writeReply", {
          mentorId,
          replyTo,
          bodyMd: letter.bodyMd,
          attachments: letter.attachments,
        });
      }
      return call(followUpId !== undefined ? "writeFollowUp" : "writeLetter", letter);
    },
    gcTime: Infinity,
    onError: () => queryClient.invalidateQueries({ queryKey: ["listModelConfigs"] }),
  });
  const latest = useMutationState<MutationState<Letter, Error, WritingEdit>>({
    filters: { mutationKey },
    select: (m) => m.state,
  }).at(-1);
  /** Drops the request and its answer; one still to come is not shown. */
  function forget() {
    const cache = queryClient.getMutationCache();
    for (const m of cache.findAll({ mutationKey })) cache.remove(m);
  }
  function start(letter: WritingEdit) {
    forget();
    write.mutate(letter);
  }
  return { latest, start, forget };
}

/**
 * "AI 起草" for a blank letter and "AI 改写" otherwise. Before the first call to a model's provider,
 * says what goes there and waits for the user to agree.
 */
export function AiButton({
  blank,
  disabled,
  onWrite,
  modelId,
}: {
  blank: boolean;
  disabled: boolean;
  onWrite: () => void;
  modelId?: number | null;
}) {
  return (
    <ModelAction modelId={modelId} disabled={disabled} onWrite={onWrite}>
      <AiIcon />
      {blank ? "AI 起草" : "AI 改写"}
    </ModelAction>
  );
}

/** The same provider consent for every AI entry point. */
export function ModelAction({
  modelId,
  modelIds,
  children,
  disabled,
  onWrite,
}: {
  modelId?: number | null;
  modelIds?: number[];
  children: ReactNode;
  disabled: boolean;
  onWrite: () => void;
}) {
  const queryClient = useQueryClient();
  const models = useQuery({ queryKey: ["listModelConfigs"], queryFn: () => call("listModelConfigs", {}) });
  const providers = useModelProviders();
  const agree = useMutation({
    // Only to the provider and address the user was told about, should the configuration have changed since.
    mutationFn: (m: ModelConfig) => call("agreeModel", { id: m.id, provider: m.provider, baseUrl: m.baseUrl }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["listModelConfigs"] }),
  });
  const fallback = models.data?.find((m) => m.isDefault);
  const required =
    modelIds && modelIds.length > 0
      ? modelIds.map((id) => models.data?.find((m) => m.id === id) ?? fallback).filter((m) => m !== undefined)
      : [models.data?.find((m) => m.id === modelId) ?? fallback].filter((m) => m !== undefined);
  const chosen = required.find((m) => !m.agreed) ?? required[0];
  const button = (onClick?: () => void) => (
    <Button
      variant="ai"
      className="px-3"
      disabled={disabled || !models.data || !providers.data || agree.isPending}
      onClick={onClick}
    >
      {children}
    </Button>
  );
  const error = models.error ?? providers.error ?? agree.error;
  return (
    <>
      {!chosen ? (
        <Popover>
          <PopoverTrigger render={button()} />
          <PopoverContent align="start" className="w-72 p-3 text-sm">
            尚未添加模型，可在
            <Link to="/settings/models" className="text-brand-text">
              设置 › AI › 模型
            </Link>
            里添加。
          </PopoverContent>
        </Popover>
      ) : !chosen.agreed ? (
        <Confirm
          trigger={button()}
          message={notice(chosen, providers.data ?? [])}
          action="同意并继续"
          onConfirm={() =>
            agree.mutate(chosen, {
              onSuccess: () => {
                if (required.every((m) => m.id === chosen.id || m.agreed)) onWrite();
              },
            })
          }
        />
      ) : (
        button(onWrite)
      )}
      {error && <p className="text-sm text-destructive">{errorMessage(error)}</p>}
    </>
  );
}

/** What AI features send, and where (docs/design.md#个人资料和配置). */
function notice(model: ModelConfig, providers: ModelProvider[]): string {
  // A custom configuration is known by its address.
  const where = providers.find((p) => p.id === model.provider)?.name ?? model.baseUrl;
  return `AI 写信、分析匹配度这类功能会把用到的内容发给“${where}”：你的个人资料、材料里的文字、导师的资料、抓取的网页标题和文字及链接文字，以及要改写、回复或追问参考的邮件。同意后，用这个模型时不再询问。`;
}

const aiFactCheck = { queryKey: ["getAiFactCheck"], queryFn: () => call("getAiFactCheck", {}) };

/**
 * AI 核对 of a draft, after a letter AI wrote is put in it and saved, when the setting is on
 * (docs/design.md#联系). Like the writing, the check outlives the composer. The result is kept on
 * the draft, and what it found comes back with the checks named `checks`.
 */
export function useFactCheck(draftKey: readonly unknown[], checks: string, flush: () => Promise<void>) {
  const queryClient = useQueryClient();
  const mutationKey = ["factCheckEmail", ...draftKey];
  const check = useMutation({
    mutationKey,
    // One check at a time, so that the result kept is that of the latest.
    scope: { id: mutationKey.join(" ") },
    mutationFn: async () => {
      if (!(await queryClient.ensureQueryData(aiFactCheck))) return null;
      await flush();
      const draft = queryClient.getQueryData<{ id: number } | null>(draftKey);
      return draft ? call("factCheckEmail", { id: draft.id }) : null;
    },
    // Settles with the draft read again, so that a failure is not shown twice nor a result late.
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: draftKey }),
        queryClient.invalidateQueries({ queryKey: [checks] }),
      ]),
  });
  const latest = useMutationState<MutationState<FactCheck | null, Error, void>>({
    filters: { mutationKey },
    select: (m) => m.state,
  }).at(-1);
  return { latest, start: () => check.mutate(), forget: () => dropFactChecks(queryClient, draftKey) };
}

/** Drops the checks of a draft sent or written anew meanwhile; their answers are not shown. */
export function dropFactChecks(queryClient: QueryClient, draftKey: readonly unknown[]) {
  const cache = queryClient.getMutationCache();
  for (const m of cache.findAll({ mutationKey: ["factCheckEmail", ...draftKey] })) cache.remove(m);
}

/** AI 核对 underway, or why it failed, with the way to try again (docs/ui.md#交互). */
export function FactCheckStatus({
  check,
  stored,
}: {
  check: ReturnType<typeof useFactCheck>;
  stored: FactCheck | null | undefined;
}) {
  // A failure kept from before the setting was turned off is of no more use.
  const on = useQuery(aiFactCheck);
  if (check.latest?.status === "pending") return <p className="text-sm text-muted-foreground">AI 正在核对…</p>;
  // A result kept since, such as that of a draft written anew, outdates an earlier failure.
  const reason =
    check.latest?.status === "error" && stored?.state !== "completed"
      ? errorMessage(check.latest.error)
      : on.data && stored?.state === "failed"
        ? stored.reason
        : null;
  return (
    reason !== null && (
      <p className="text-sm text-destructive">
        AI 核对未能完成：{reason}
        <Button variant="quiet" size="sm" onClick={check.start}>
          重试
        </Button>
      </p>
    )
  );
}

/** What replacing the letter with the one AI wrote would change. */
export function Rewrite({ before, after }: { before: Letter; after: Letter }) {
  const subject = changes(before.subject, after.subject);
  const body = changes(letterText(before.bodyMd), letterText(after.bodyMd));
  const facts = [...subject, ...body].some((c) => c.fact);
  return (
    <div className="flex flex-col gap-3 rounded-lg bg-muted px-4 py-3">
      <p className="flex items-center gap-2 text-sm font-medium text-brand-text">
        <AiIcon />
        AI 改写的结果
      </p>
      <Changes parts={subject} className="font-medium" />
      <Changes parts={body} />
      {facts && (
        <p className="flex items-start gap-2 text-sm text-warning">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" strokeWidth={1.8} />
          改动里有日期、数字或链接，已标黄，请核对后再采用。
        </p>
      )}
    </div>
  );
}

/** Two four-pointed stars, as in the app's icon (docs/ui.md#尺寸). */
export function AiIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="size-[18px] shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M10.5 4.8Q11.46 11.52 16.5 12.8Q11.46 14.08 10.5 20.8Q9.54 14.08 4.5 12.8Q9.54 11.52 10.5 4.8Z" />
      <path d="M18.6 2.4Q19.02 4.64 20.7 5.2Q19.02 5.76 18.6 8Q18.18 5.76 16.5 5.2Q18.18 4.64 18.6 2.4Z" />
    </svg>
  );
}
