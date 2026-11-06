import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import type { PlanCloud, PlanColumn, PlanEmail, Waiting } from "@/api/types";
import { call, errorMessage } from "@/api/call";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/batch-bar";
import { CloudSendButton, WithdrawFromCloud } from "@/components/cloud-send";
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
  mailbox_secret: "恢复后需要重新填写邮箱授权码",
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
      {column === "upcoming" && (
        <div className="flex items-center gap-2 px-3 pb-2">
          <CloudSendButton />
          <span className="text-xs text-muted-foreground">电脑关机也按时发出</span>
        </div>
      )}
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-2 pb-2">
        {query.data?.emails.map((e) =>
          e.cloud ? (
            <CloudRow key={e.id} email={e} cloud={e.cloud} />
          ) : (
            <PlanRow key={e.id} email={e} column={column} />
          ),
        )}
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
    mutationFn: async (op: "withdrawEmail" | "retryEmail" | "confirmUnsentEmail" | "retryTaskGeneration") => {
      if (op === "retryTaskGeneration") {
        if (e.taskId !== null) await call(op, { taskId: e.taskId, id: e.id });
      } else await call(op, { id: e.id });
    },
    onSuccess: (_, op) => {
      if (op === "withdrawEmail") dropDrafts(client, [e.mentorId], e.kind);
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
        {column === "history" ? `${e.fromAddress ?? ""}${e.byCloud ? "（云端）" : ""}` : (e.taskName ?? "单独首封")}
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
          {e.cancelReason !== null && ` 已取消：${e.cancelReason}。确认没发出后，不再发送。`}
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
                  {e.cancelReason === null ? "确认没发出后重新发送" : "确认没发出"}
                </Button>
              }
              message={
                e.cancelReason === null
                  ? "确认这封邮件没发出？如果之前已经发出，导师会收到两封。"
                  : `已取消：${e.cancelReason}。确认这封邮件没发出后，将结束这封邮件，不再发送。`
              }
              action={e.cancelReason === null ? "重新发送" : "确认没发出"}
              onConfirm={() => action.mutate(e.cancelReason === null ? "retryEmail" : "confirmUnsentEmail")}
            />
          )}
        </div>
      )}
      {action.error && <p className="text-xs text-destructive">{errorMessage(action.error)}</p>}
    </article>
  );
}

/** An email with the official server: it can only be withdrawn. */
function CloudRow({ email: e, cloud }: { email: PlanEmail; cloud: PlanCloud }) {
  return (
    <article className="min-w-0 space-y-2 rounded-md bg-background p-3">
      <p className="text-sm font-medium tabular-nums">
        {day(cloud.at) === "今天" ? time(cloud.at) : scheduledAt(cloud.at)}
      </p>
      <Link to="/mentors/$mentorId" params={{ mentorId: e.mentorId }} className="block min-w-0">
        <p className="truncate font-medium">{e.name}</p>
        <p className="truncate text-sm">{e.subject}</p>
      </Link>
      <p className="truncate text-xs text-muted-foreground">{e.taskName ?? "单独首封"}</p>
      <p className="text-xs text-muted-foreground">
        {cloud.uploading ? "正在交给云端" : "已交给云端"}，从 {e.fromAddress} 发出
        {e.cancelReason !== null && `；已取消：${e.cancelReason}，正在从云端撤回`}
      </p>
      {cloud.pause && (
        <p className="text-xs text-warning">
          云端暂停到 {scheduledAt(cloud.pause.until)}：{cloud.pause.reason}
        </p>
      )}
      {e.cancelReason === null && (
        <div className="flex flex-wrap gap-1">
          <WithdrawFromCloud withdraw={{ email: e.id }} />
        </div>
      )}
    </article>
  );
}
