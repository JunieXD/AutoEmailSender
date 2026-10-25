import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import type { PlanColumn, PlanEmail, Waiting } from "@/api/types";
import { call, errorMessage } from "@/api/call";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/batch-bar";
import { Pager } from "@/components/mentor-table";
import { ScheduleActions, refreshSchedule, scheduledAt } from "@/components/schedule";
import { dropDrafts } from "@/components/mentor";
import { day, time } from "@/format";

const COLUMNS: [PlanColumn, string][] = [
  ["pending", "待处理"],
  ["upcoming", "即将发送"],
  ["history", "历史"],
];
const WAITING: Record<Waiting, string> = {
  time: "时间未到",
  mailbox_limit: "邮箱今天已到上限",
  mailbox_paused: "邮箱已暂停",
  task_paused: "任务已暂停",
  mailbox_interval: "等待邮箱发送间隔",
  no_mailbox: "尚无可用邮箱",
  ready: "等待发送",
};

export function SendingPlan() {
  const search = useSearch({ from: "/tasks" });
  const navigate = useNavigate({ from: "/tasks" });
  const selected = search.column ?? "pending";
  return (
    <>
      <div className="mx-4 mb-3 hidden rounded-full bg-muted p-1 phone:flex" role="tablist" aria-label="发送计划">
        {COLUMNS.map(([column, label]) => (
          <button
            key={column}
            role="tab"
            aria-selected={selected === column}
            className="h-10 flex-1 rounded-full text-sm aria-selected:bg-background aria-selected:shadow-sm"
            onClick={() => void navigate({ search: (s) => ({ ...s, column }) })}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-3 gap-4 px-5 pb-4 max-[1240px]:gap-2 max-[1240px]:px-3 phone:grid-cols-1 phone:px-3">
        {COLUMNS.map(([column, label]) => (
          <PlanList key={column} column={column} label={label} hidden={column !== selected} />
        ))}
      </div>
    </>
  );
}
function PlanList({ column, label, hidden }: { column: PlanColumn; label: string; hidden: boolean }) {
  const search = useSearch({ from: "/tasks" });
  const navigate = useNavigate({ from: "/tasks" });
  const page = search[column] ?? 1;
  const query = useQuery({
    queryKey: ["sendingPlan", column, page],
    queryFn: () => call("sendingPlan", { column, page }),
    refetchInterval: 3000,
  });
  return (
    <section className={`flex min-h-0 min-w-0 flex-col rounded-lg bg-muted/50 ${hidden ? "phone:hidden" : ""}`}>
      <h2 className={`px-3 py-3 font-medium phone:hidden ${column === "pending" ? "text-warning" : ""}`}>
        {label}
        {query.data && <span className="ml-2 text-sm text-muted-foreground">{query.data.total}</span>}
      </h2>
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-2 pb-2">
        {query.data?.emails.map((e) => (
          <PlanRow key={e.id} email={e} column={column} />
        ))}
        {query.data?.total === 0 && (
          <p className="p-3 text-sm text-muted-foreground">
            {column === "pending"
              ? "当前没有待处理邮件"
              : column === "upcoming"
                ? "批准邮件后，发送时间会列在这里"
                : "尚无发送记录"}
          </p>
        )}
        {query.error && <p className="p-3 text-sm text-destructive">{errorMessage(query.error)}</p>}
      </div>
      <div className="flex justify-end p-2">
        <Pager
          page={page}
          total={query.data?.total ?? 0}
          onPage={(n) => navigate({ search: (s) => ({ ...s, [column]: n }) })}
        />
      </div>
    </section>
  );
}
function PlanRow({ email: e, column }: { email: PlanEmail; column: PlanColumn }) {
  const client = useQueryClient();
  const action = useMutation({
    mutationFn: async (op: "withdrawEmail" | "retryEmail" | "retryTaskGeneration") => {
      if (op === "retryTaskGeneration") {
        if (e.taskId !== null) await call(op, { taskId: e.taskId, id: e.id });
      } else await call(op, { id: e.id });
    },
    onSuccess: (_, op) => {
      if (op === "withdrawEmail") dropDrafts(client, [e.mentorId]);
      refreshSchedule(client);
    },
  });
  const link =
    column !== "history" && e.taskId !== null ? (
      <Link to="/tasks/$id" params={{ id: e.taskId }} search={{ email: e.id }} className="block min-w-0">
        <p className="truncate font-medium">{e.name}</p>
        <p className="truncate text-sm">{e.subject ?? "草稿尚未生成"}</p>
      </Link>
    ) : (
      <Link to="/mentors/$mentorId" params={{ mentorId: e.mentorId }} className="block min-w-0">
        <p className="truncate font-medium">{e.name}</p>
        <p className="truncate text-sm">{e.subject ?? "草稿尚未生成"}</p>
      </Link>
    );
  return (
    <article className="min-w-0 space-y-2 rounded-md bg-background p-3">
      {column === "upcoming" && e.plannedAt !== null && (
        <p className="text-sm font-medium tabular-nums">
          {day(e.plannedAt) === "今天" ? time(e.plannedAt) : scheduledAt(e.plannedAt)}
        </p>
      )}
      {column === "history" && e.sentAt !== null && (
        <p className="text-xs text-muted-foreground">{scheduledAt(e.sentAt)}</p>
      )}
      {link}
      <p className="truncate text-xs text-muted-foreground">
        {column === "history" ? e.fromAddress : (e.taskName ?? "单独首封")}
      </p>
      {e.waiting !== null && <p className="text-xs text-muted-foreground">{WAITING[e.waiting]}</p>}
      {column === "pending" && (
        <p className="text-xs text-warning">
          {e.state === "approved"
            ? e.latestAt !== null
              ? `${scheduledAt(e.latestAt)} 前未能发出`
              : "所选窗口已无空位，请修改发送时间或改期"
            : e.state === "generation_failed"
              ? `生成失败：${e.error}`
              : e.state === "maybe_sent"
                ? `可能已发出：${e.error}`
                : `发送失败：${e.error}`}
        </p>
      )}
      {column !== "history" && (
        <div className="flex flex-wrap gap-1">
          {e.state === "approved" && <ScheduleActions id={e.id} initial={e.plannedAt ?? undefined} />}
          {e.resendOf === null && (e.state === "approved" || e.state === "failed") && (
            <Button
              variant="ghost"
              size="sm"
              disabled={action.isPending}
              onClick={() => action.mutate("withdrawEmail")}
            >
              改回草稿
            </Button>
          )}
          {e.state === "failed" && (
            <Button variant="quiet" size="sm" disabled={action.isPending} onClick={() => action.mutate("retryEmail")}>
              重试
            </Button>
          )}
          {e.state === "generation_failed" && (
            <Button
              variant="quiet"
              size="sm"
              disabled={action.isPending}
              onClick={() => action.mutate("retryTaskGeneration")}
            >
              重试
            </Button>
          )}
          {e.state === "maybe_sent" && (
            <Confirm
              trigger={
                <Button variant="quiet" size="sm" disabled={action.isPending}>
                  确认未发出后重新发送
                </Button>
              }
              message="确认这封邮件未发出？如果之前已经发出，导师会收到两封。"
              action="重新发送"
              onConfirm={() => action.mutate("retryEmail")}
            />
          )}
        </div>
      )}
      {action.error && <p className="text-xs text-destructive">{errorMessage(action.error)}</p>}
    </article>
  );
}
