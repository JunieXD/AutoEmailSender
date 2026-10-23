import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { Tag } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogBody, DialogContent, DialogHeader } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

/** docs/ui.md#颜色: the colours a tag can have; new tags take them in turn. */
export const TAG_COLORS = [
  "#991b1b",
  "#c2410c",
  "#985a0c",
  "#2c7a4f",
  "#0e7490",
  "#3f6aa8",
  "#7e22ce",
  "#6f6762",
] as const;

/** The colour for the next new tag: the presets in turn. */
function nextColor(count: number): string {
  return TAG_COLORS[count % TAG_COLORS.length] ?? TAG_COLORS[0];
}

export function TagChip({ tag }: { tag: Pick<Tag, "name" | "color"> }) {
  return (
    <span
      className="rounded-full px-2 py-0.5 text-xs whitespace-nowrap"
      // Mixed with the text colour so the same tag reads well in light and dark mode.
      style={{
        color: `color-mix(in oklab, ${tag.color} 65%, var(--foreground))`,
        backgroundColor: `color-mix(in oklab, ${tag.color} 14%, transparent)`,
      }}
    >
      {tag.name}
    </span>
  );
}

function useTags() {
  return useQuery({ queryKey: ["listTags"], queryFn: () => call("listTags", {}) });
}

/** After tags change, everything that shows them is out of date. */
function refreshTags(queryClient: ReturnType<typeof useQueryClient>) {
  for (const queryKey of [["listTags"], ["getMentor"], ["listMentors"]]) {
    void queryClient.invalidateQueries({ queryKey });
  }
}

/** Ticks a mentor's tags on and off, adds new ones, and opens the tag manager. */
export function TagPicker({ mentorId, selected }: { mentorId: number; selected: Tag[] }) {
  const [managing, setManaging] = useState(false);
  return (
    <>
      <Popover>
        <PopoverTrigger
          render={
            <Button variant="quiet" size="sm" className="text-muted-foreground hover:text-foreground">
              编辑
            </Button>
          }
        />
        <PopoverContent align="end">
          <TagChoices mentorId={mentorId} selected={selected} onManage={() => setManaging(true)} />
        </PopoverContent>
      </Popover>
      {managing && <TagManager onClose={() => setManaging(false)} />}
    </>
  );
}

/** Mounted while the picker is open, so it starts from the mentor's current tags. */
function TagChoices({ mentorId, selected, onManage }: { mentorId: number; selected: Tag[]; onManage: () => void }) {
  const queryClient = useQueryClient();
  const tags = useTags();
  const [ids, setIds] = useState(() => selected.map((t) => t.id));
  const [name, setName] = useState("");
  const save = useMutation({
    // One at a time, so quick clicks land in order.
    scope: { id: `setMentorTags:${mentorId}` },
    mutationFn: (tagIds: number[]) => call("setMentorTags", { mentorId, tagIds }),
    onSuccess: () => refreshTags(queryClient),
  });
  const create = useMutation({
    mutationFn: () => call("createTag", { name, color: nextColor(tags.data?.length ?? 0) }),
    onSuccess: (tag) => {
      setName("");
      choose([...ids, tag.id]);
    },
  });
  function choose(next: number[]) {
    setIds(next);
    save.mutate(next);
  }
  const error = save.error ?? create.error;
  return (
    <div className="flex flex-col gap-1">
      {tags.data?.map((t) => (
        <label key={t.id} className="flex items-center gap-2.5 rounded-md px-2 py-1.5 hover:bg-muted">
          <Checkbox
            checked={ids.includes(t.id)}
            onCheckedChange={(on) => choose(on ? [...ids, t.id] : ids.filter((id) => id !== t.id))}
          />
          <TagChip tag={t} />
        </label>
      ))}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) create.mutate();
        }}
      >
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="新建标签，按回车添加"
          aria-label="新建标签"
          className="h-9"
          disabled={create.isPending}
        />
      </form>
      {error && <p className="px-2 text-destructive">{errorMessage(error)}</p>}
      <Button variant="ghost" className="h-8 justify-start px-2 font-normal" onClick={onManage}>
        管理标签
      </Button>
    </div>
  );
}

/** Renames, recolours, reorders and deletes tags. */
function TagManager({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const tags = useTags();
  const [error, setError] = useState<string>();
  const run = useMutation({
    mutationFn: (op: () => Promise<unknown>) => op(),
    onSuccess: () => setError(undefined),
    onError: (e) => setError(errorMessage(e)),
    onSettled: () => refreshTags(queryClient),
  });
  const list = tags.data ?? [];
  function move(index: number, by: -1 | 1) {
    // Half a place past the neighbour, so sorting puts the tag on the neighbour's other side.
    const ids = list
      .map((t, i) => ({ id: t.id, at: i === index ? i + by * 1.5 : i }))
      .toSorted((a, b) => a.at - b.at)
      .map((t) => t.id);
    run.mutate(() => call("reorderTags", { ids }));
  }
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="w-[480px]">
        <DialogHeader>管理标签</DialogHeader>
        <DialogBody className="flex flex-col gap-1.5 pb-5">
          {list.length === 0 && <p className="py-6 text-center text-muted-foreground">还没有标签</p>}
          {list.map((t, i) => (
            <TagRow
              key={t.id}
              tag={t}
              first={i === 0}
              last={i === list.length - 1}
              onChange={(name, color) => run.mutate(() => call("updateTag", { id: t.id, name, color }))}
              onMove={(by) => move(i, by)}
              onDelete={() => run.mutate(() => call("deleteTag", { id: t.id }))}
            />
          ))}
          {error && <p className="text-sm text-destructive">{error}</p>}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}

function TagRow({
  tag,
  first,
  last,
  onChange,
  onMove,
  onDelete,
}: {
  tag: Tag;
  first: boolean;
  last: boolean;
  onChange: (name: string, color: string) => void;
  onMove: (by: -1 | 1) => void;
  onDelete: () => void;
}) {
  const [name, setName] = useState(tag.name);
  const [confirming, setConfirming] = useState(false);
  const rename = () => {
    if (name.trim() !== tag.name) onChange(name, tag.color);
  };
  return (
    <div className="flex items-center gap-2">
      <Popover>
        <PopoverTrigger render={<Button variant="ghost" size="icon" aria-label={`“${tag.name}”的颜色`} />}>
          <span className="size-4 rounded-full" style={{ backgroundColor: tag.color }} />
        </PopoverTrigger>
        <PopoverContent align="start" className="flex w-auto gap-1">
          {TAG_COLORS.map((c) => (
            <button
              key={c}
              type="button"
              aria-label={c}
              aria-pressed={c === tag.color}
              className="grid size-8 place-items-center rounded-md hover:bg-muted aria-pressed:bg-muted"
              onClick={() => onChange(tag.name, c)}
            >
              <span className="size-4 rounded-full" style={{ backgroundColor: c }} />
            </button>
          ))}
        </PopoverContent>
      </Popover>
      <Input
        value={name}
        onChange={(e) => setName(e.target.value)}
        onBlur={rename}
        onKeyDown={(e) => e.key === "Enter" && rename()}
        aria-label="标签名称"
        className="h-9"
      />
      <Button variant="ghost" size="icon" aria-label="上移" disabled={first} onClick={() => onMove(-1)}>
        <ChevronUp strokeWidth={1.6} />
      </Button>
      <Button variant="ghost" size="icon" aria-label="下移" disabled={last} onClick={() => onMove(1)}>
        <ChevronDown strokeWidth={1.6} />
      </Button>
      {confirming ? (
        <Button
          variant="ghost"
          className="px-3 text-destructive hover:text-destructive"
          onClick={onDelete}
          onBlur={() => setConfirming(false)}
        >
          确认删除
        </Button>
      ) : (
        <Button variant="ghost" className="px-3" onClick={() => setConfirming(true)}>
          删除
        </Button>
      )}
    </div>
  );
}
