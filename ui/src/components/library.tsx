import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { call, errorMessage } from "@/api/call";
import type {
  Decisions,
  LibraryAddPreview,
  LibraryAdded,
  LibraryStatus,
  LibraryUpdate,
  LibraryUpdateField,
} from "@/api/types";
import { useAccountRecheck } from "@/components/account";
import { Differences } from "@/components/differences";
import { IntakeChoices } from "@/components/intake-choices";
import { refreshMentors } from "@/components/mentor";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { sameDifference } from "@/import-decisions";

export const LIBRARY_STATUS: Record<LibraryStatus, string> = {
  active: "在职",
  retired: "已退休",
  departed: "已离职",
  deceased: "已去世",
  removed: "已撤下",
};

const UPDATE_FIELDS: Record<LibraryUpdateField, string> = {
  name: "姓名",
  research: "研究方向",
  sourceUrl: "来源网址",
  title: "职称",
  primaryEmail: "发信地址",
  primaryAffiliation: "主要单位",
};

const EMPTY: Decisions = { take: [], matches: [], restore: [] };

export function refreshLibrary(client: QueryClient) {
  for (const key of ["getLibraryOrgs", "listLibraryMentors", "previewLibraryMentor", "getLibraryQuota"])
    void client.invalidateQueries({ queryKey: [key] });
  refreshMentors(client);
}

/** Whether adding these needs the user's choices first, as importing a table would. */
export function needsChoices(preview: LibraryAddPreview): boolean {
  return preview.differences.length + preview.maybeSame.length + preview.trashed.length + preview.errors.length > 0;
}

/** "已添加 12 位，补充了 3 位已有导师的资料", for the top of the page after adding. */
export function addedMessage(r: LibraryAdded): string {
  return (
    [
      r.created > 0 && `已添加 ${r.created} 位`,
      r.supplemented > 0 && `补充了 ${r.supplemented} 位已有导师的资料`,
      r.missing > 0 && `${r.missing} 位已不在导师库，没有添加`,
    ]
      .filter(Boolean)
      .join("，") || "所选导师都已在导师列表里，资料没有变化"
  );
}

/** The import dialog's choices for mentors taken from the library (docs/ui.md#交互). */
export function LibraryAddDialog({
  ids,
  initial,
  onClose,
  onDone,
}: {
  ids: string[];
  /** The preview with the default choices, already read to decide whether to ask. */
  initial: LibraryAddPreview;
  onClose: () => void;
  onDone: (result: LibraryAdded) => void;
}) {
  const [decisions, setDecisions] = useState<Decisions | null>(null);
  const again = useQuery({
    queryKey: ["previewLibraryAdd", ids, decisions],
    queryFn: () => call("previewLibraryAdd", { ids, decisions: decisions ?? undefined }),
    enabled: decisions !== null,
    placeholderData: keepPreviousData,
  });
  const preview = again.data ?? initial;
  const add = useMutation({
    mutationFn: () => call("addLibraryMentors", { ids, decisions: decisions ?? undefined }),
    onSuccess: onDone,
  });
  useAccountRecheck(again.error ?? add.error);
  const differenceIds = new Set(preview.differences.map((d) => d.id));
  const possible = preview.created + preview.updated + preview.unchanged > 0;
  return (
    <Dialog
      open
      onOpenChange={(open, event) => {
        if (!open && !add.isPending && event.reason !== "outside-press" && event.reason !== "escape-key") onClose();
      }}
    >
      <DialogContent className="w-[920px]">
        <DialogHeader>从导师库添加 · {ids.length} 位</DialogHeader>
        <DialogBody className={`space-y-5 ${again.isFetching ? "pointer-events-none opacity-50" : ""}`}>
          <div className="flex flex-wrap gap-x-5 gap-y-1 rounded-lg bg-muted p-3 text-sm" role="status">
            <span>
              新增 <strong>{preview.created}</strong> 位
            </span>
            <span>
              补充资料 <strong>{preview.supplemented}</strong> 位
            </span>
            <span>
              资料不同 <strong>{differenceIds.size}</strong> 位
            </span>
            <span>
              没有变化 <strong>{preview.unchanged}</strong> 位
            </span>
          </div>
          <IntakeChoices
            preview={preview}
            decisions={decisions ?? EMPTY}
            onChange={(next) => setDecisions((d) => (typeof next === "function" ? next(d ?? EMPTY) : next))}
            source="导师库"
            numbered={false}
          />
          {preview.errors.length > 0 && (
            <section>
              <h3 className="mb-2 font-medium">不能添加 · {preview.errors.length} 位</h3>
              <ul className="space-y-1 text-sm text-destructive">
                {preview.errors.map((e) => (
                  <li key={e.row}>
                    {e.name}：{e.reason}
                  </li>
                ))}
              </ul>
            </section>
          )}
          {preview.missing > 0 && <p className="text-sm text-warning">有 {preview.missing} 位已不在导师库，不会添加</p>}
        </DialogBody>
        <DialogFooter className="flex-wrap">
          <p className="min-w-0 grow text-sm text-destructive" role="alert">
            {(again.error ?? add.error) && errorMessage(again.error ?? add.error)}
          </p>
          <DialogClose render={<Button variant="ghost" disabled={add.isPending} />}>取消</DialogClose>
          <Button
            disabled={add.isPending || again.isFetching || again.isError || !possible}
            onClick={() => add.mutate()}
          >
            {add.isPending
              ? "正在添加…"
              : `添加${possible ? `（新增 ${preview.created} 位，更新 ${preview.updated} 位）` : ""}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Background › 待处理: the library changed values these mentors also have, and the user picks (docs/ui.md#交互). */
export function LibraryUpdatesDialog({ onClose }: { onClose: () => void }) {
  const client = useQueryClient();
  const updates = useQuery({ queryKey: ["listLibraryUpdates"], queryFn: () => call("listLibraryUpdates", {}) });
  const items = updates.data?.flatMap((g) => g.differences) ?? [];
  const [take, setTake] = useState<LibraryUpdate[] | null>(null);
  const chosen = take ?? items.filter((d) => d.take);
  const mentors = new Set(chosen.map((d) => d.id)).size;
  const apply = useMutation({
    mutationFn: () =>
      call("applyLibraryUpdates", {
        choices: items.map((d) => ({ ...d, take: chosen.some((c) => sameDifference(c, d)) })),
      }),
    onSuccess: onClose,
    onSettled: () => refreshLibrary(client),
  });
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !apply.isPending) onClose();
      }}
    >
      <DialogContent className="w-[760px]">
        <DialogHeader>导师库的更新</DialogHeader>
        <DialogBody className="space-y-4">
          {updates.data && items.length === 0 && <p className="text-sm text-muted-foreground">没有要处理的更新</p>}
          {items.length > 0 && (
            <>
              <p className="text-sm text-muted-foreground">
                导师库改了这些导师的资料，和你这里的不同。你没改过的项默认用导师库的，改过的默认保留你的；关掉不做改动，以后再选。
              </p>
              <Differences
                source="导师库"
                numbered={false}
                names={UPDATE_FIELDS}
                items={items}
                take={chosen}
                onChange={setTake}
              />
            </>
          )}
        </DialogBody>
        <DialogFooter className="flex-wrap">
          <p className="min-w-0 grow text-sm text-destructive" role="alert">
            {(updates.error ?? apply.error) && errorMessage(updates.error ?? apply.error)}
          </p>
          <DialogClose render={<Button variant="ghost" disabled={apply.isPending} />}>关闭</DialogClose>
          <Button disabled={items.length === 0 || apply.isPending} onClick={() => apply.mutate()}>
            {apply.isPending ? "正在更新…" : mentors > 0 ? `更新（${mentors} 位）` : "都保留本地的"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
