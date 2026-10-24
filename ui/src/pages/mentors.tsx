import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { Merge, Plus, Search, Trash2 } from "lucide-react";
import { useEffect, useEffectEvent, useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { ContactStatus } from "@/api/types";
import { BatchBar, Confirm } from "@/components/batch-bar";
import { EMPTY_FORM, MentorDialog } from "@/components/mentor-dialog";
import { MergeDialog } from "@/components/merge-dialog";
import { dropDrafts, refreshMentors } from "@/components/mentor";
import { MentorTable, Pager, pageParam, useLastPage } from "@/components/mentor-table";
import { AddTagButton, useAddTag } from "@/components/tags";
import { CreateTaskDialog } from "@/components/create-task";
import { MatchingDialog, MatchingProgress } from "@/components/matching";
import { AiIcon } from "@/components/ai-writing";
import { Notice } from "@/components/notice";
import { Button, buttonVariants } from "@/components/ui/button";

const STATUSES: [ContactStatus | undefined, string][] = [
  [undefined, "全部"],
  ["none", "未联系"],
  ["contacted", "已联系"],
  ["replied", "已回复"],
  ["bounced", "退信"],
];

export type MentorSearch = { status?: ContactStatus; q?: string; page?: number };

/** The list's filters live in the URL so they survive going back; anything unexpected is dropped. */
export function mentorSearch(search: Record<string, unknown>): MentorSearch {
  const status = STATUSES.find(([s]) => s !== undefined && s === search.status)?.[0];
  const q = typeof search.q === "string" && search.q !== "" ? search.q : undefined;
  return { status, q, page: pageParam(search.page) };
}

export function MentorsPage() {
  const search = useSearch({ from: "/mentors" });
  const navigate = useNavigate({ from: "/mentors" });
  const page = search.page ?? 1;
  const query = useQuery({
    queryKey: ["listMentors", search],
    queryFn: () => call("listMentors", { status: search.status, keyword: search.q, page }),
    placeholderData: keepPreviousData,
  });
  const total = query.data?.total ?? 0;
  const queryClient = useQueryClient();
  const [task, setTask] = useState<number[] | null>(null);
  const [matching, setMatching] = useState<number[] | null>(null);
  const [adding, setAdding] = useState(false);
  // Ticks belong to one filter: changing it, including by going back, starts over.
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set());
  const [merging, setMerging] = useState<[number, number] | null>(null);
  const [first, second] = selected;
  const [filter, setFilter] = useState({ status: search.status, q: search.q });
  if (filter.status !== search.status || filter.q !== search.q) {
    setFilter({ status: search.status, q: search.q });
    setSelected(new Set());
  }
  useLastPage(query.isSuccess && !query.isFetching ? query.data.total : undefined, page, (n) =>
    navigate({ search: (s) => ({ ...s, page: n > 1 ? n : undefined }), replace: true }),
  );
  const addTag = useAddTag();
  const trash = useMutation({
    mutationFn: (ids: number[]) => call("trashMentors", { ids }),
    // Only these: others may have been ticked while it ran.
    onSuccess: (_, ids) => {
      setSelected((s) => new Set([...s].filter((id) => !ids.includes(id))));
      dropDrafts(queryClient, ids);
    },
    onSettled: () => refreshMentors(queryClient),
  });

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex items-center gap-4 px-7 pt-6 pb-4 phone:flex-wrap phone:gap-y-2.5 phone:px-4 phone:pt-3.5 phone:pb-2.5">
        <div>
          <h1 className="text-2xl font-semibold phone:text-[22px]">导师</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">{query.data ? `共 ${total.toLocaleString()} 位` : " "}</p>
        </div>
        <div className="grow" />
        <SearchBox
          value={search.q ?? ""}
          onChange={(q) => navigate({ search: (s) => ({ ...s, q: q || undefined, page: undefined }) })}
        />
        <Button aria-label="添加导师" className="phone:px-2.5" onClick={() => setAdding(true)}>
          <Plus strokeWidth={1.6} />
          <span className="phone:hidden">添加导师</span>
        </Button>
        {adding && (
          <MentorDialog
            title="添加导师"
            action="添加"
            initial={EMPTY_FORM}
            save={(form) => call("addMentor", form)}
            onSaved={({ id }) => {
              void queryClient.invalidateQueries({ queryKey: ["listMentors"] });
              void navigate({ to: "/mentors/$mentorId", params: { mentorId: id } });
            }}
            onClose={() => setAdding(false)}
          />
        )}
      </header>

      <div className="px-7 pb-3 phone:overflow-x-auto phone:px-4 phone:pb-2.5 phone:[scrollbar-width:none]">
        <div role="tablist" aria-label="联系状态" className="inline-flex gap-0.5 rounded-full bg-muted p-[3px]">
          {STATUSES.map(([status, label]) => (
            <button
              key={label}
              role="tab"
              aria-selected={search.status === status}
              onClick={() => navigate({ search: (s) => ({ ...s, status, page: undefined }) })}
              className="h-[30px] rounded-full px-3.5 text-sm whitespace-nowrap phone:h-10 text-muted-foreground aria-selected:bg-background aria-selected:font-medium aria-selected:text-foreground aria-selected:shadow-sm dark:aria-selected:bg-input"
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <MatchingProgress />
      <div className="relative flex min-h-0 flex-1 flex-col">
        <div
          className={`@container min-h-0 flex-1 overflow-y-auto px-4 phone:px-0 ${selected.size > 0 ? "pb-20" : ""}`}
        >
          {query.isError ? (
            <Notice title="导师列表没能加载" detail={errorMessage(query.error)} />
          ) : query.data?.total === 0 ? (
            search.status || search.q ? (
              <Notice title="没有符合条件的导师" detail="换个关键词或联系状态再试。" />
            ) : (
              <Notice title="还没有导师" detail="点“添加导师”填写导师的资料。" />
            )
          ) : (
            <MentorTable
              mentors={query.data?.mentors ?? []}
              selected={selected}
              selectable={!query.isPlaceholderData}
              onSelect={setSelected}
            />
          )}
        </div>
        <BatchBar
          count={selected.size}
          errors={[
            ...(addTag.error ? [`没能添加标签：${errorMessage(addTag.error)}`] : []),
            ...(trash.error ? [`没能移入回收站：${errorMessage(trash.error)}`] : []),
          ]}
          onClear={() => {
            setSelected(new Set());
            for (const action of [addTag, trash]) if (!action.isPending) action.reset();
          }}
        >
          <Button
            variant="ghost"
            onClick={() => {
              const id = [...selected][0];
              if (selected.size === 1 && id !== undefined)
                void navigate({ to: "/mentors/$mentorId", params: { mentorId: id } });
              else setTask([...selected]);
            }}
          >
            写信
          </Button>
          <Button variant="ghost" onClick={() => setMatching([...selected])}>
            <AiIcon />
            分析匹配度
          </Button>
          <AddTagButton mentorIds={[...selected]} add={addTag} />
          {selected.size === 2 && first !== undefined && second !== undefined && (
            <Button variant="ghost" onClick={() => setMerging([first, second])}>
              <Merge strokeWidth={1.6} />
              合并
            </Button>
          )}
          <Confirm
            trigger={
              <Button variant="ghost" disabled={trash.isPending}>
                <Trash2 strokeWidth={1.6} />
                移入回收站
              </Button>
            }
            message={`把选中的 ${selected.size} 位导师移入回收站？没发出的邮件会取消，恢复导师后也不会恢复。`}
            action="移入回收站"
            onConfirm={() => trash.mutate([...selected])}
          />
        </BatchBar>
        {task && <CreateTaskDialog ids={task} onClose={() => setTask(null)} />}
        {matching && <MatchingDialog ids={matching} onClose={() => setMatching(null)} />}
        {merging && <MergeDialog ids={merging} onClose={() => setMerging(null)} />}
      </div>

      <footer className="flex items-center justify-between gap-2 px-7 py-3 text-sm text-muted-foreground phone:px-4">
        <Link to="/mentors/trash" className={buttonVariants({ variant: "ghost", className: "-ml-3 px-3" })}>
          <Trash2 strokeWidth={1.6} />
          回收站
        </Link>
        <Pager
          page={page}
          total={total}
          onPage={(n) => navigate({ search: (s) => ({ ...s, page: n > 1 ? n : undefined }) })}
        />
      </footer>
    </div>
  );
}

function SearchBox({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [text, setText] = useState(value);
  // Follow the URL when it changes from elsewhere, such as going back.
  const [shown, setShown] = useState(value);
  if (value !== shown) {
    setShown(value);
    setText(value);
  }
  const commit = useEffectEvent(onChange);
  useEffect(() => {
    const timer = setTimeout(() => {
      if (text.trim() !== value) commit(text.trim());
    }, 300);
    return () => clearTimeout(timer);
  }, [text, value]);
  return (
    <label className="flex h-10 w-[320px] items-center gap-2 rounded-full bg-muted px-4 text-muted-foreground focus-within:ring-2 focus-within:ring-ring phone:order-3 phone:w-full">
      <Search className="size-[18px] shrink-0" strokeWidth={1.6} />
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="搜索姓名、学校、研究方向"
        aria-label="搜索导师"
        className="min-w-0 flex-1 bg-transparent text-foreground outline-none placeholder:text-faint"
      />
    </label>
  );
}
