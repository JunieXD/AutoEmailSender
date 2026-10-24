import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch, useParams, useBlocker } from "@tanstack/react-router";
import { useEffect, useRef, useState, type RefObject } from "react";
import { ChevronLeft, MoreHorizontal } from "lucide-react";
import { call, errorMessage } from "@/api/call";
import type { DraftFilter, DraftState, Progress, TaskSummary } from "@/api/types";
import { ModelAction } from "@/components/ai-writing";
import { Confirm } from "@/components/batch-bar";
import { type ComposerHandle } from "@/components/letter-composer";
import { TaskLetter } from "@/components/task-letter";
import { Avatar } from "@/components/mentor";
import { Pager, pageParam } from "@/components/mentor-table";
import { Notice } from "@/components/notice";
import { usePlaceholders } from "@/components/template-dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { NativeSelect } from "@/components/ui/native-select";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { day } from "@/format";
import { letterHtml, letterText } from "@/letter";

export function tasksSearch(s: Record<string, unknown>): { page?: number } {
  return { page: pageParam(s.page) };
}
export function taskSearch(s: Record<string, unknown>): { email?: number; page?: number; filter?: DraftFilter } {
  return {
    email: typeof s.email === "number" && Number.isSafeInteger(s.email) && s.email > 0 ? s.email : undefined,
    page: pageParam(s.page),
    filter:
      s.filter === "review" || s.filter === "problems" || s.filter === "generation_failed" || s.filter === "approved"
        ? s.filter
        : undefined,
  };
}
export function refreshTasks(client: ReturnType<typeof useQueryClient>) {
  for (const key of ["taskWork", "listTasks", "getTask", "listTaskDrafts", "getTaskDraft", "planTask"])
    void client.invalidateQueries({ queryKey: [key] });
}
const SEGMENTS: [keyof Progress, string, string][] = [
  ["generating", "生成中", "bg-info"],
  ["review", "待审核", "bg-faint"],
  ["generationFailed", "生成失败", "bg-destructive"],
  ["approved", "待发送", "bg-warning"],
  ["sent", "已发出", "bg-success"],
  ["failed", "发送失败", "bg-destructive"],
  ["canceled", "已取消", "bg-input"],
];
function ProgressBar({ progress, labels = false }: { progress: Progress; labels?: boolean }) {
  const total = Object.values(progress).reduce((n, v) => n + v, 0);
  return (
    <div className="min-w-0">
      <div
        className="flex h-2.5 overflow-hidden rounded-full bg-muted"
        aria-label={SEGMENTS.map(([k, label]) => `${label} ${progress[k]} 封`).join("，")}
      >
        {SEGMENTS.map(
          ([k, label, color]) =>
            progress[k] > 0 && (
              <div
                key={k}
                className={color}
                style={{ width: `${(progress[k] / total) * 100}%` }}
                title={`${label} ${progress[k]} 封`}
              />
            ),
        )}
      </div>
      {labels && (
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
          {SEGMENTS.map(
            ([k, label, color]) =>
              progress[k] > 0 && (
                <span key={k} className="flex items-center gap-1">
                  <span className={`size-1.5 rounded-full ${color}`} />
                  {label} {progress[k]}
                </span>
              ),
          )}
        </div>
      )}
    </div>
  );
}
const TASK_STATE = { active: "进行中", paused: "已暂停", stopped: "已停止", completed: "已完成" };
export function TasksPage() {
  const search = useSearch({ from: "/tasks" });
  const navigate = useNavigate({ from: "/tasks" });
  const page = search.page ?? 1;
  const query = useQuery({
    queryKey: ["listTasks", page],
    queryFn: () => call("listTasks", { page }),
    refetchInterval: 3000,
  });
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="px-7 pt-6 pb-5 phone:px-4">
        <h1 className="text-2xl font-semibold">任务</h1>
        <p className="mt-1 text-sm text-muted-foreground">审核首封草稿，批准后依次发出</p>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 phone:px-2">
        {query.error ? (
          <Notice title="任务列表未能加载" detail={errorMessage(query.error)} />
        ) : query.data?.total === 0 ? (
          <Notice title="尚未创建任务" detail="在导师列表勾选多位导师，按“写信”创建任务。" />
        ) : (
          query.data?.tasks.map((t) => (
            <Link
              key={t.id}
              to="/tasks/$id"
              params={{ id: t.id }}
              className="mb-[3px] grid grid-cols-[minmax(140px,1fr)_minmax(160px,1.5fr)_70px] items-center gap-5 rounded-md px-4 py-4 hover:bg-muted phone:grid-cols-[minmax(0,1fr)_64px] phone:gap-2"
            >
              <div className="min-w-0">
                <h2 className="truncate font-medium">{t.name}</h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  {day(t.createdAt)} · {Object.values(t.progress).reduce((n, v) => n + v, 0)} 位导师
                </p>
              </div>
              <div className="phone:col-span-2 phone:row-start-2">
                <ProgressBar progress={t.progress} labels />
              </div>
              <span className={`text-sm ${t.state === "paused" ? "text-warning" : "text-muted-foreground"}`}>
                {TASK_STATE[t.state]}
              </span>
            </Link>
          ))
        )}
      </div>
      <footer className="flex justify-end px-7 py-3">
        <Pager
          page={page}
          total={query.data?.total ?? 0}
          pageSize={20}
          onPage={(p) => navigate({ search: { page: p > 1 ? p : undefined } })}
        />
      </footer>
    </div>
  );
}
const FILTERS: [DraftFilter, string][] = [
  ["all", "全部"],
  ["review", "待审核"],
  ["problems", "有问题"],
  ["generation_failed", "生成失败"],
  ["approved", "已批准"],
];
const DRAFT_STATE: Record<DraftState, string> = {
  generating: "生成中",
  generation_failed: "生成失败",
  review: "待审核",
  approved: "待发送",
  cloud: "云端待发",
  sending: "发送中",
  maybe_sent: "可能已发出",
  sent: "已发出",
  bounced: "退信",
  failed: "发送失败",
  canceled: "已取消",
};
export function TaskReviewPage() {
  const { id } = useParams({ from: "/tasks/$id" });
  const search = useSearch({ from: "/tasks/$id" });
  const navigate = useNavigate({ from: "/tasks/$id" });
  const client = useQueryClient();
  const composer = useRef<ComposerHandle>(null);
  const [error, setError] = useState<string | null>(null);
  const [bulk, setBulk] = useState(false);
  const filter = search.filter ?? "all";
  const page = search.page ?? 1;
  const task = useQuery({ queryKey: ["getTask", id], queryFn: () => call("getTask", { id }), refetchInterval: 3000 });
  const list = useQuery({
    queryKey: ["listTaskDrafts", id, filter, page],
    queryFn: () => call("listTaskDrafts", { taskId: id, filter, page }),
    refetchInterval: 3000,
  });
  // Every router navigation, including the sidebar and browser back, shares the save boundary.
  useBlocker({
    enableBeforeUnload: () => client.isMutating({ mutationKey: ["saveTaskDraft"] }) > 0,
    shouldBlockFn: async () => {
      try {
        await composer.current?.flush();
        return false;
      } catch (e) {
        setError(errorMessage(e));
        return true;
      }
    },
  });
  const current = search.email ?? list.data?.drafts[0]?.id;
  useEffect(() => {
    const phone = matchMedia("(max-width: 760px)");
    function openFirst() {
      if (!search.email && current !== undefined && !phone.matches)
        void navigate({ search: (s) => ({ ...s, email: current }), replace: true });
    }
    openFirst();
    phone.addEventListener("change", openFirst);
    return () => phone.removeEventListener("change", openFirst);
  }, [current, search.email, navigate]);
  async function flush() {
    await composer.current?.flush();
  }
  function choose(email?: number) {
    void navigate({ search: (s) => ({ ...s, email }) });
  }
  function move(delta: number) {
    const drafts = list.data?.drafts ?? [];
    const index = drafts.findIndex((d) => d.id === current);
    const next = drafts[index + delta];
    if (next) choose(next.id);
    else if (delta > 0 && page * 50 < (list.data?.total ?? 0))
      void navigate({ search: (s) => ({ ...s, page: page + 1, email: undefined }) });
    else if (delta < 0 && page > 1) void navigate({ search: (s) => ({ ...s, page: page - 1, email: undefined }) });
  }
  useEffect(() => {
    function key(e: KeyboardEvent) {
      if (
        e.defaultPrevented ||
        e.metaKey ||
        e.ctrlKey ||
        e.altKey ||
        (e.target instanceof Element && e.target.closest("input,textarea,select,[contenteditable=true],[role=dialog]"))
      )
        return;
      if (document.querySelector('[role="dialog"]')) return;
      if (e.key.toLowerCase() === "j") {
        e.preventDefault();
        move(1);
      }
      if (e.key.toLowerCase() === "k") {
        e.preventDefault();
        move(-1);
      }
      if (e.key.toLowerCase() === "a") {
        e.preventDefault();
        composer.current?.approve();
      }
    }
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  });
  const action = useMutation({
    mutationFn: async (op: "pauseTask" | "resumeTask" | "stopTask" | "deleteTask" | "retryTaskGeneration") => {
      await flush();
      if (op === "retryTaskGeneration") await call(op, { taskId: id, id: null });
      else await call(op, { id });
      if (op === "deleteTask") await navigate({ to: "/tasks" });
    },
    onSuccess: () => refreshTasks(client),
  });
  if (task.error) return <Notice title="任务未能加载" detail={errorMessage(task.error)} />;
  const t = task.data;
  if (!t) return null;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="space-y-3 border-b border-border px-5 py-4 phone:px-3">
        <div className="flex items-center gap-2">
          <Link to="/tasks" aria-label="返回任务列表" className="rounded-md p-2 hover:bg-muted">
            <ChevronLeft className="size-5" />
          </Link>
          <h1 className="min-w-0 grow truncate text-lg font-semibold">{t.name}</h1>
          <span className="text-xs text-muted-foreground phone:hidden">{TASK_STATE[t.state]}</span>
          {t.state !== "stopped" &&
            (t.state === "paused" && t.writer === "ai" ? (
              <ModelAction
                modelId={t.modelConfigId}
                disabled={action.isPending}
                onWrite={() => action.mutate("resumeTask")}
              >
                继续
              </ModelAction>
            ) : (
              <Button
                variant="quiet"
                disabled={action.isPending}
                onClick={() => action.mutate(t.state === "paused" ? "resumeTask" : "pauseTask")}
              >
                {t.state === "paused" ? "继续" : "暂停"}
              </Button>
            ))}
          <Popover>
            <PopoverTrigger render={<Button variant="ghost" size="icon" aria-label="更多" />}>
              <MoreHorizontal />
            </PopoverTrigger>
            <PopoverContent align="end" className="flex w-64 flex-col gap-2 p-2">
              {t.state !== "stopped" && (
                <Confirm
                  trigger={<Button variant="ghost">停止任务</Button>}
                  message="停止后，未发出的邮件全部取消，任务无法继续。正在发送的邮件会完成发送。"
                  action="停止任务"
                  onConfirm={() => action.mutate("stopTask")}
                />
              )}
              <Confirm
                trigger={
                  <Button variant="ghost" className="text-destructive">
                    删除任务
                  </Button>
                }
                message="删除任务和未发出的邮件？发出过的邮件保留在导师往来中。有正在发送的邮件时无法删除。"
                action="删除任务"
                onConfirm={() => action.mutate("deleteTask")}
              />
            </PopoverContent>
          </Popover>
        </div>
        <ProgressBar progress={t.progress} labels />
        {t.state === "paused" && (
          <p className="text-sm text-warning">
            {t.pauseReason ?? "你已暂停任务。可以继续审核和批准，继续任务后才会发出。"}
          </p>
        )}
        {(error || action.error) && (
          <p role="alert" className="text-sm text-destructive">
            {error ?? errorMessage(action.error)}
          </p>
        )}
      </header>
      <div className="grid min-h-0 flex-1 grid-cols-[250px_minmax(0,1fr)_280px] max-[1240px]:grid-cols-[230px_minmax(0,1fr)] phone:grid-cols-1">
        <section
          className={`flex min-h-0 flex-col border-r border-border phone:border-0 ${search.email ? "phone:hidden" : ""}`}
        >
          <div className="space-y-2 p-3">
            <NativeSelect
              aria-label="筛选草稿"
              value={filter}
              onChange={(e) => {
                const next = FILTERS.find(([v]) => v === e.target.value)?.[0];
                void navigate({ search: { filter: next === "all" ? undefined : next, page: undefined } });
              }}
            >
              {FILTERS.map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </NativeSelect>
            <Button
              variant="quiet"
              className="w-full"
              disabled={t.progress.review === 0 || t.state === "stopped"}
              onClick={() => {
                void flush()
                  .then(() => setBulk(true))
                  .catch((e: unknown) => setError(errorMessage(e)));
              }}
            >
              批准全部待审核
            </Button>
            {t.progress.generationFailed > 0 && t.state !== "stopped" && (
              <Button variant="ghost" className="w-full" onClick={() => action.mutate("retryTaskGeneration")}>
                重试全部生成失败的
              </Button>
            )}
          </div>
          <div className="min-h-0 flex-1 space-y-[3px] overflow-y-auto px-2">
            {list.error ? (
              <Notice title="草稿列表未能加载" detail={errorMessage(list.error)} />
            ) : list.data?.total === 0 ? (
              <Notice title="没有符合条件的邮件" detail="换个筛选条件查看。" />
            ) : (
              list.data?.drafts.map((d) => (
                <button
                  key={d.id}
                  onClick={() => choose(d.id)}
                  aria-current={current === d.id}
                  className={`flex w-full items-center gap-3 rounded-md px-3 py-3 text-left ${current === d.id ? "bg-selected" : "hover:bg-muted"}`}
                >
                  <Avatar id={d.mentorId} name={d.name} />
                  <span className="min-w-0 grow">
                    <span className="block truncate font-medium">{d.name}</span>
                    <span className="mt-1 block text-xs text-muted-foreground">
                      {DRAFT_STATE[d.state]}
                      {d.problems > 0 && <span className="ml-2 text-warning">{d.problems} 个问题</span>}
                    </span>
                  </span>
                </button>
              ))
            )}
          </div>
          <footer className="flex justify-end p-3">
            <Pager
              page={page}
              total={list.data?.total ?? 0}
              onPage={(n) => navigate({ search: (s) => ({ ...s, page: n, email: undefined }) })}
            />
          </footer>
        </section>
        {current ? (
          <DraftDetail
            key={current}
            id={current}
            task={t}
            composer={composer}
            mobileOpen={search.email !== undefined}
            onBack={() => choose(undefined)}
            onApproved={() => {
              refreshTasks(client);
              move(1);
            }}
          />
        ) : (
          <Notice title="选择一封草稿开始审核" detail="草稿会陆续出现在左边。" />
        )}
      </div>
      {bulk && <BulkApproval task={t} onClose={() => setBulk(false)} />}
    </div>
  );
}
function DraftDetail({
  id,
  task,
  composer,
  mobileOpen,
  onBack,
  onApproved,
}: {
  id: number;
  task: TaskSummary;
  composer: RefObject<ComposerHandle | null>;
  mobileOpen: boolean;
  onBack: () => void;
  onApproved: () => void;
}) {
  const client = useQueryClient();
  const key = { taskId: task.id, id };
  const draft = useQuery({
    queryKey: ["getTaskDraft", id],
    queryFn: () => call("getTaskDraft", key),
    refetchInterval: 3000,
  });
  const m = useQuery({
    queryKey: ["getMentor", draft.data?.mentorId],
    queryFn: () => call("getMentor", { id: draft.data?.mentorId ?? 0 }),
    enabled: draft.data !== undefined,
  });
  const materials = useQuery({ queryKey: ["listMaterials"], queryFn: () => call("listMaterials", {}) });
  const placeholders = usePlaceholders();
  const action = useMutation({
    mutationFn: async (
      op: "removeTaskEmail" | "regenerateTaskEmail" | "reapplyTaskTemplate" | "withdrawEmail" | "retryTaskGeneration",
    ) => {
      await composer.current?.flush();
      if (op === "withdrawEmail") await call(op, { id });
      else if (op === "retryTaskGeneration") await call(op, key);
      else await call(op, key);
    },
    onSuccess: () => refreshTasks(client),
  });
  const error = draft.error ?? m.error ?? materials.error ?? placeholders.error;
  const d = draft.data;
  const mentor = m.data;
  if (error) return <Notice title="邮件未能加载" detail={errorMessage(error)} />;
  if (!d || !mentor || !materials.data || !placeholders.data) return null;
  const editable = d.state === "review" && task.state !== "stopped";
  return (
    <>
      <section className={`min-h-0 min-w-0 overflow-y-auto ${mobileOpen ? "" : "phone:hidden"}`}>
        <div className="flex items-center gap-2 px-5 pt-4 phone:px-3">
          <Button
            variant="ghost"
            size="icon"
            aria-label="返回草稿列表"
            className="hidden phone:inline-flex"
            onClick={onBack}
          >
            <ChevronLeft />
          </Button>
          <div className="min-w-0 grow">
            <p className="font-medium">{mentor.name}</p>
            <p className="truncate text-sm text-muted-foreground">{mentor.emails[0] ?? "未填写邮箱"}</p>
          </div>
          <span className="shrink-0 text-xs text-muted-foreground">{DRAFT_STATE[d.state]}</span>
        </div>
        {d.state === "generating" ? (
          <Notice
            title="AI 正在写"
            detail={task.state === "paused" ? "任务已暂停，继续后生成排队中的草稿。" : "可以先审核其他草稿。"}
          />
        ) : d.state === "generation_failed" ? (
          <div className="space-y-3 p-5">
            <p className="text-warning">生成失败：{d.error}</p>
            <Button
              disabled={action.isPending || task.state === "stopped"}
              onClick={() => action.mutate("retryTaskGeneration")}
            >
              重试
            </Button>
          </div>
        ) : (
          <TaskLetter
            key={`${id}-${d.state}`}
            ref={composer}
            mentor={mentor}
            draft={d}
            placeholders={placeholders.data}
            materials={materials.data}
            task={task}
            onApproved={onApproved}
            actions={
              editable && (
                <Confirm
                  trigger={
                    <Button variant="ghost" disabled={action.isPending}>
                      {task.writer === "ai" ? "重新生成" : "重新套用模板"}
                    </Button>
                  }
                  message="替换当前这封的主题和正文？修改过的内容将被替换，仍需重新审核。"
                  action="替换"
                  onConfirm={() => action.mutate(task.writer === "ai" ? "regenerateTaskEmail" : "reapplyTaskTemplate")}
                />
              )
            }
          />
        )}
        <div className="flex flex-wrap gap-2 px-5 pb-5 phone:px-3">
          {["generating", "generation_failed", "review", "approved", "failed"].includes(d.state) &&
            task.state !== "stopped" && (
              <Confirm
                trigger={
                  <Button variant="ghost" disabled={action.isPending}>
                    移出任务
                  </Button>
                }
                message="移出这位导师？这封邮件将取消，导师资料保留。"
                action="移出任务"
                onConfirm={() => action.mutate("removeTaskEmail")}
              />
            )}
          {(d.state === "approved" || d.state === "failed") && task.state !== "stopped" && (
            <Button variant="quiet" onClick={() => action.mutate("withdrawEmail")}>
              改回草稿
            </Button>
          )}
          {d.error && d.state !== "generation_failed" && <p className="w-full text-sm text-warning">{d.error}</p>}
          {action.error && <p className="w-full text-sm text-destructive">{errorMessage(action.error)}</p>}
        </div>
      </section>
      <aside className="min-h-0 overflow-y-auto border-l border-border p-5 text-sm max-[1240px]:hidden">
        <h2 className="mb-4 text-base font-semibold">匹配结果</h2>
        {mentor.analysis.result ? (
          <div className="space-y-4">
            <p className="text-2xl font-semibold">
              {mentor.analysis.result.score}
              <span className="ml-1 text-xs font-normal text-muted-foreground">分</span>
            </p>
            <p>{mentor.analysis.result.reason}</p>
            {(
              [
                ["契合方向", mentor.analysis.result.directions],
                ["建议强调", mentor.analysis.result.highlights],
                ["风险", mentor.analysis.result.risks],
              ] satisfies [string, string[]][]
            ).map(
              ([label, items]) =>
                items.length > 0 && (
                  <div key={label}>
                    <h3 className="mb-2 text-muted-foreground">{label}</h3>
                    <ul className="space-y-2">
                      {items.map((s) => (
                        <li key={s}>{s}</li>
                      ))}
                    </ul>
                  </div>
                ),
            )}
          </div>
        ) : (
          <p className="text-muted-foreground">尚未分析匹配度</p>
        )}
        <Link to="/mentors/$mentorId" params={{ mentorId: mentor.id }} className="mt-5 inline-block text-brand-text">
          查看导师资料
        </Link>
      </aside>
    </>
  );
}
function BulkApproval({ task, onClose }: { task: TaskSummary; onClose: () => void }) {
  const client = useQueryClient();
  const [skip, setSkip] = useState(true);
  const drafts = useQuery({
    queryKey: ["prepareTaskApproval", task.id],
    queryFn: () => call("prepareTaskApproval", { id: task.id }),
    staleTime: Infinity,
  });
  const style = useQuery({ queryKey: ["getLetterStyle"], queryFn: () => call("getLetterStyle", {}) });
  const chosen = drafts.data?.filter((d) => !skip || d.problems === 0) ?? [];
  const problematic = drafts.data?.filter((d) => d.problems > 0).length ?? 0;
  const approve = useMutation({
    mutationFn: async () => {
      if (!style.data) return;
      await call("approveTaskEmails", {
        taskId: task.id,
        emails: chosen.map((d) => ({
          id: d.id,
          attachments: d.attachments,
          subject: d.subject,
          bodyMd: d.bodyMd,
          html: letterHtml(d.bodyMd, style.data),
          text: letterText(d.bodyMd),
        })),
      });
    },
    onSuccess: () => {
      refreshTasks(client);
      onClose();
    },
  });
  return (
    <Dialog open onOpenChange={(open) => !open && !approve.isPending && onClose()}>
      <DialogContent className="w-[480px]">
        <DialogHeader>批准全部待审核</DialogHeader>
        <DialogBody className="space-y-3 py-4">
          <p>
            当前有 {drafts.data?.length ?? 0} 封待审核，其中 {problematic} 封有问题
          </p>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={skip} onCheckedChange={setSkip} />
            跳过有问题的邮件
          </label>
          <p className="text-sm text-muted-foreground">
            将批准 {chosen.length} 封。
            {task.state === "paused"
              ? "任务已暂停，继续任务后才会发出。"
              : "批准后立即交给发信循环，按邮箱余量依次发出。"}
            之后生成的草稿不包括在内。
          </p>
          <p className="text-sm text-muted-foreground">附件按各封草稿当前的清单发送，请在批准前核对。</p>
          {(drafts.error ?? style.error ?? approve.error) && (
            <p className="text-sm text-destructive">{errorMessage(drafts.error ?? style.error ?? approve.error)}</p>
          )}
        </DialogBody>
        <DialogFooter className="justify-end">
          <Button variant="ghost" onClick={onClose} disabled={approve.isPending}>
            取消
          </Button>
          <Button
            disabled={!drafts.data || !style.data || chosen.length === 0 || approve.isPending}
            onClick={() => approve.mutate()}
          >
            批准 {chosen.length} 封
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
