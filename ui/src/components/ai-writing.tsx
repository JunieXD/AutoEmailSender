import { type MutationState, useMutation, useMutationState, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { TriangleAlert } from "lucide-react";
import { call, errorMessage } from "@/api/call";
import type { FirstDraftEdit, Letter, ModelConfig, ModelProvider } from "@/api/types";
import { Confirm } from "@/components/batch-bar";
import { useModelProviders } from "@/components/model-dialog";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { type Change, changes } from "@/diff";
import { letterText } from "@/letter";

// AI writing in a composer (docs/ui.md#交互).

/**
 * The model writing a mentor's letter. The request and its answer stay in the mutation cache
 * until forgotten, so that leaving the page neither stops it nor loses what comes back.
 */
export function useWriting(mentorId: number) {
  const queryClient = useQueryClient();
  const mutationKey = ["writeLetter", mentorId];
  const write = useMutation({
    mutationKey,
    mutationFn: (letter: FirstDraftEdit) => call("writeLetter", letter),
    gcTime: Infinity,
  });
  const latest = useMutationState<MutationState<Letter, Error, FirstDraftEdit>>({
    filters: { mutationKey },
    select: (m) => m.state,
  }).at(-1);
  /** Drops the request and its answer; one still to come is not shown. */
  function forget() {
    const cache = queryClient.getMutationCache();
    for (const m of cache.findAll({ mutationKey })) cache.remove(m);
  }
  function start(letter: FirstDraftEdit) {
    forget();
    write.mutate(letter);
  }
  return { latest, start, forget };
}

/**
 * "AI 起草" for a blank letter and "AI 改写" otherwise. Before the first call to a model's provider,
 * says what goes there and waits for the user to agree.
 */
export function AiButton({ blank, disabled, onWrite }: { blank: boolean; disabled: boolean; onWrite: () => void }) {
  const queryClient = useQueryClient();
  const models = useQuery({ queryKey: ["listModelConfigs"], queryFn: () => call("listModelConfigs", {}) });
  const providers = useModelProviders();
  const agree = useMutation({
    // Only to the provider and address the user was told about, should the configuration have changed since.
    mutationFn: (m: ModelConfig) => call("agreeModel", { id: m.id, provider: m.provider, baseUrl: m.baseUrl }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["listModelConfigs"] }),
  });
  const chosen = models.data?.find((m) => m.isDefault);
  const button = (onClick?: () => void) => (
    <Button
      variant="ai"
      className="px-3"
      disabled={disabled || !models.data || !providers.data || agree.isPending}
      onClick={onClick}
    >
      <AiIcon />
      {blank ? "AI 起草" : "AI 改写"}
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
          onConfirm={() => agree.mutate(chosen, { onSuccess: onWrite })}
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
  return `AI 写信、分析匹配度这类功能会把用到的内容发给“${where}”：你的个人资料、材料里的文字、导师的资料，以及要改写或回复的邮件。同意后，用这个模型时不再询问。`;
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

function Changes({ parts, className }: { parts: Change[]; className?: string }) {
  return (
    <p className={`leading-relaxed break-words whitespace-pre-wrap ${className ?? ""}`}>
      {parts.map((c, i) =>
        c.kind === "kept" ? (
          <span key={i}>{c.text}</span>
        ) : c.kind === "removed" ? (
          <del key={i} className={`rounded-sm text-muted-foreground ${c.fact ? "bg-highlight" : "bg-diff-delete"}`}>
            {c.text}
          </del>
        ) : (
          <ins key={i} className={`rounded-sm no-underline ${c.fact ? "bg-highlight" : "bg-diff-insert"}`}>
            {c.text}
          </ins>
        ),
      )}
    </p>
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
