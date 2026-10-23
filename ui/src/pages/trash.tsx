import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCanGoBack, useNavigate, useRouter, useSearch } from "@tanstack/react-router";
import { ChevronLeft, RotateCcw, Trash2 } from "lucide-react";
import { useState } from "react";
import { call, errorMessage } from "@/api/call";
import { BatchBar, Confirm } from "@/components/batch-bar";
import { refreshMentors } from "@/components/mentor";
import { MentorTable, Pager, pageParam, useLastPage } from "@/components/mentor-table";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";

export function trashSearch(search: Record<string, unknown>): { page?: number } {
  return { page: pageParam(search.page) };
}

/** Mentors set aside, most recently trashed first; they can be restored or deleted for good (docs/ui.md#页面). */
export function TrashPage() {
  const search = useSearch({ from: "/mentors/trash" });
  const navigate = useNavigate({ from: "/mentors/trash" });
  const router = useRouter();
  const canGoBack = useCanGoBack();
  const page = search.page ?? 1;
  const query = useQuery({
    queryKey: ["listTrash", page],
    queryFn: () => call("listTrash", { page }),
    placeholderData: keepPreviousData,
  });
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set());
  useLastPage(query.isSuccess && !query.isFetching ? query.data.total : undefined, page, (n) =>
    navigate({ search: { page: n > 1 ? n : undefined }, replace: true }),
  );
  const act = useMutation({
    mutationFn: ({ op, ids }: { op: "restoreMentors" | "deleteMentors"; ids: number[] }) => call(op, { ids }),
    // Only these: others may have been ticked while it ran.
    onSuccess: (_, { ids }) => setSelected((s) => new Set([...s].filter((id) => !ids.includes(id)))),
    onSettled: () => refreshMentors(queryClient),
  });
  const failed = act.variables?.op === "restoreMentors" ? "没能恢复" : "没能永久删除";

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex items-center gap-3 px-5 pt-5 pb-4 phone:px-3 phone:pt-3">
        <Button
          variant="ghost"
          size="icon"
          aria-label="返回导师列表"
          onClick={() => (canGoBack ? router.history.back() : router.navigate({ to: "/mentors" }))}
        >
          <ChevronLeft strokeWidth={1.6} />
        </Button>
        <div>
          <h1 className="text-2xl font-semibold phone:text-[22px]">回收站</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {query.data ? `共 ${query.data.total.toLocaleString()} 位` : " "}
          </p>
        </div>
      </header>

      <div className="relative flex min-h-0 flex-1 flex-col">
        <div
          className={`@container min-h-0 flex-1 overflow-y-auto px-4 phone:px-0 ${selected.size > 0 ? "pb-20" : ""}`}
        >
          {query.isError ? (
            <Notice title="回收站没能加载" detail={errorMessage(query.error)} />
          ) : query.data?.total === 0 ? (
            <Notice title="回收站是空的" detail="移入回收站的导师会出现在这里，可以恢复或永久删除。" />
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
          errors={act.error ? [`${failed}：${errorMessage(act.error)}`] : []}
          onClear={() => {
            setSelected(new Set());
            if (!act.isPending) act.reset();
          }}
        >
          <Button
            variant="ghost"
            disabled={act.isPending}
            onClick={() => act.mutate({ op: "restoreMentors", ids: [...selected] })}
          >
            <RotateCcw strokeWidth={1.6} />
            恢复
          </Button>
          <Confirm
            trigger={
              <Button variant="ghost" disabled={act.isPending}>
                <Trash2 strokeWidth={1.6} />
                永久删除
              </Button>
            }
            message={`永久删除选中的 ${selected.size} 位导师？他们的邮件、往来和匹配结果会一起删除，不能撤销；邮箱里的原信不受影响。`}
            action="永久删除"
            onConfirm={() => act.mutate({ op: "deleteMentors", ids: [...selected] })}
          />
        </BatchBar>
      </div>

      <footer className="flex items-center justify-end px-7 py-3 text-sm text-muted-foreground phone:justify-center phone:px-4">
        <Pager
          page={page}
          total={query.data?.total ?? 0}
          onPage={(n) => navigate({ search: { page: n > 1 ? n : undefined } })}
        />
      </footer>
    </div>
  );
}
