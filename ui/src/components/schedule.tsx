import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { call, errorMessage } from "@/api/call";
import type { Schedule, TaskSummary } from "@/api/types";
import { Field, formClose } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Input } from "@/components/ui/input";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { Confirm } from "@/components/batch-bar";
import { day, time } from "@/format";

export function localDate(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
function tomorrow(): Date {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(9, 0, 0, 0);
  return d;
}
export function defaultSchedule(): Schedule {
  return { kind: "window", dates: [localDate(tomorrow())], start: "09:00", end: "11:00", perWindow: 10 };
}
export function scheduleValid(s: Schedule): boolean {
  return (
    s.kind === "now" || (s.dates.length > 0 && s.start < s.end && s.perWindow > 0 && Number.isInteger(s.perWindow))
  );
}
export function useScheduleCapacity(schedule: Schedule) {
  const enabled = schedule.kind === "window" && scheduleValid(schedule);
  const capacity = useQuery({
    queryKey: ["scheduleCapacity", schedule],
    queryFn: () => (schedule.kind === "window" ? call("scheduleCapacity", schedule) : null),
    enabled,
    refetchOnWindowFocus: "always",
  });
  const { refetch } = capacity;
  const nextChangeAt = capacity.data?.nextChangeAt;
  useEffect(() => {
    if (!enabled || nextChangeAt == null) return undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refreshAtBoundary = () => {
      const wait = nextChangeAt - Date.now();
      // Browser timers cannot wait more than 2^31 - 1 ms in one go.
      if (wait > 0) timer = setTimeout(refreshAtBoundary, Math.min(wait, 2_147_483_647));
      else void refetch({ cancelRefetch: false });
    };
    refreshAtBoundary();
    return () => clearTimeout(timer);
  }, [enabled, nextChangeAt, refetch]);
  useEffect(() => {
    if (!enabled) return undefined;
    const refresh = () => void refetch({ cancelRefetch: false });
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [enabled, refetch]);
  return capacity;
}
export function scheduledAt(at: number): string {
  return `${day(at)} ${time(at)}`;
}
export function scheduleText(s: Schedule): string {
  if (s.kind === "now") return "批准后立即发送";
  return `${s.dates
    .toSorted()
    .map((d) => day(new Date(`${d}T12:00`).getTime()))
    .join("、")}，每天 ${s.start}–${s.end}，每次最多 ${s.perWindow} 封`;
}

export function ScheduleFields({
  value,
  onChange,
  count,
}: {
  value: Schedule;
  onChange: (s: Schedule) => void;
  count?: number;
}) {
  const [today] = useState(() => new Date(new Date().setHours(0, 0, 0, 0)));
  const capacity = useScheduleCapacity(value);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Button
          variant={value.kind === "now" ? "quiet" : "ghost"}
          aria-pressed={value.kind === "now"}
          onClick={() => onChange({ kind: "now" })}
        >
          批准后立即发送
        </Button>
        <Button
          variant={value.kind === "window" ? "quiet" : "ghost"}
          aria-pressed={value.kind === "window"}
          onClick={() => onChange(defaultSchedule())}
        >
          定时发送
        </Button>
      </div>
      {value.kind === "window" && (
        <>
          <div className="flex flex-wrap items-start gap-5">
            <div>
              <p className="mb-2 text-sm text-muted-foreground">发送日期（可多选）</p>
              <Calendar
                mode="multiple"
                selected={value.dates.map((d) => new Date(`${d}T12:00`))}
                disabled={(date) => date < today && !value.dates.includes(localDate(date))}
                onSelect={(dates) => onChange({ ...value, dates: (dates ?? []).map(localDate).toSorted() })}
              />
            </div>
            <div className="min-w-[100px] flex-1 space-y-3">
              <Field id="window-start" label="开始时间">
                <Input
                  id="window-start"
                  type="time"
                  value={value.start}
                  onChange={(e) => onChange({ ...value, start: e.target.value })}
                />
              </Field>
              <Field id="window-end" label="结束时间">
                <Input
                  id="window-end"
                  type="time"
                  value={value.end}
                  onChange={(e) => onChange({ ...value, end: e.target.value })}
                />
              </Field>
              <Field id="window-count" label="每个窗口最多几封">
                <Input
                  id="window-count"
                  type="number"
                  min={1}
                  value={value.perWindow}
                  onChange={(e) => onChange({ ...value, perWindow: Number(e.target.value) })}
                />
              </Field>
            </div>
          </div>
          {capacity.data && scheduleValid(value) && (
            <p className="text-sm">
              共 {capacity.data.windows} 个可用窗口，最多 {capacity.data.emails} 封
            </p>
          )}
          {capacity.isFetching && !capacity.data && <p className="text-sm text-muted-foreground">正在计算可用容量…</p>}
          {capacity.error && (
            <p role="alert" className="text-sm text-destructive">
              {errorMessage(capacity.error)}
            </p>
          )}
          {!scheduleValid(value) && <p className="text-sm text-warning">请选择日期、有效时间窗口和每个窗口的数量</p>}
          {count !== undefined && capacity.data && scheduleValid(value) && capacity.data.emails < count && (
            <p role="alert" className="text-sm text-warning">
              容量不足，还差 {count - capacity.data.emails} 封，请增加日期或每个窗口的数量
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            按本机当前时区排期。错过窗口会排进后面的空位，没有空位的留待处理。
          </p>
        </>
      )}
    </div>
  );
}

export function ChangeSchedule({ task, onClose }: { task: TaskSummary; onClose: () => void }) {
  const client = useQueryClient();
  const [value, setValue] = useState(task.schedule);
  const [saved, setSaved] = useState<number | null>(null);
  const save = useMutation({
    mutationFn: () => call("changeTaskSchedule", { id: task.id, schedule: value }),
    onSuccess: (n) => {
      setSaved(n);
      refreshSchedule(client);
    },
  });
  return (
    <Dialog open onOpenChange={formClose(true, save.isPending, onClose)}>
      <DialogContent className="w-[620px]">
        <DialogHeader>修改发送时间</DialogHeader>
        <DialogBody className="space-y-4 py-4">
          <ScheduleFields
            value={value}
            onChange={(v) => {
              setValue(v);
              setSaved(null);
            }}
          />
          <p className="text-sm text-muted-foreground">
            保存后，{task.progress.approved} 封已批准且尚未发出的邮件会按批准顺序重新排期；排不进的留待处理。
          </p>
          {saved !== null && (
            <p role="status" className="text-sm text-success">
              已重新排期 {saved} 封
            </p>
          )}
          {save.error && <p className="text-sm text-destructive">{errorMessage(save.error)}</p>}
        </DialogBody>
        <DialogFooter className="justify-end">
          <Button variant="ghost" disabled={save.isPending} onClick={onClose}>
            {saved !== null ? "关闭" : "取消"}
          </Button>
          <Button disabled={!scheduleValid(value) || save.isPending || saved !== null} onClick={() => save.mutate()}>
            保存发送时间
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function DateTimeDialog({
  title = "改期",
  initial,
  onClose,
  onConfirm,
  pending = false,
  error,
  problems = 0,
}: {
  title?: string;
  initial?: number;
  onClose: () => void;
  onConfirm: (at: number) => void;
  pending?: boolean;
  error?: unknown;
  problems?: number;
}) {
  const [now] = useState(() => Date.now());
  const [today] = useState(() => new Date(new Date().setHours(0, 0, 0, 0)));
  const [start] = useState(() => (initial !== undefined && initial > now ? new Date(initial) : tomorrow()));
  const [date, setDate] = useState<Date | undefined>(start);
  const [clock, setClock] = useState(() => time(start.getTime()));
  const selected = date ? new Date(`${localDate(date)}T${clock}`) : undefined;
  const at =
    selected && date && time(selected.getTime()) === clock && localDate(selected) === localDate(date)
      ? selected.getTime()
      : NaN;
  return (
    <Dialog open onOpenChange={formClose(true, pending, onClose)}>
      <DialogContent className="w-[380px]">
        <DialogHeader>{title}</DialogHeader>
        <DialogBody className="space-y-4 py-4">
          <Calendar mode="single" selected={date} onSelect={setDate} disabled={{ before: today }} />
          <Field id="send-time" label="发送时间">
            <Input id="send-time" type="time" value={clock} onChange={(e) => setClock(e.target.value)} />
          </Field>
          {date && clock && !Number.isFinite(at) && (
            <p className="text-sm text-warning">所选日期的这个时间不存在，请选择其他时间</p>
          )}
          <p className="text-sm text-muted-foreground">所选时间后一小时内仍未发出的，留待处理</p>
          {error != null && <p className="text-sm text-destructive">{errorMessage(error)}</p>}
        </DialogBody>
        <DialogFooter className="justify-end">
          <Button variant="ghost" disabled={pending} onClick={onClose}>
            取消
          </Button>
          {problems > 0 ? (
            <Confirm
              trigger={<Button disabled={pending || !Number.isFinite(at) || at <= now}>确认{title}</Button>}
              message={`还有 ${problems} 个问题，仍要发送？`}
              action="仍要定时发送"
              onConfirm={() => onConfirm(at)}
            />
          ) : (
            <Button disabled={pending || !Number.isFinite(at) || at <= now} onClick={() => onConfirm(at)}>
              确认{title}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function refreshSchedule(client: ReturnType<typeof useQueryClient>) {
  for (const key of [
    "sendingPlan",
    "scheduledWork",
    "getTask",
    "listTasks",
    "getTaskDraft",
    "listTaskDrafts",
    "listCorrespondence",
    "taskWork",
  ])
    void client.invalidateQueries({ queryKey: [key] });
}
export function ScheduleActions({ id, initial }: { id: number; initial?: number }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const action = useMutation({
    mutationFn: () => call("sendEmailNow", { id }),
    onSuccess: () => refreshSchedule(client),
  });
  const reschedule = useMutation({
    mutationFn: (plannedAt: number) => call("rescheduleEmail", { id, plannedAt }),
    onSuccess: () => {
      setOpen(false);
      refreshSchedule(client);
    },
  });
  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        disabled={action.isPending}
        onClick={() => {
          reschedule.reset();
          setOpen(true);
        }}
      >
        改期
      </Button>
      <Confirm
        trigger={
          <Button variant="ghost" size="sm" disabled={action.isPending}>
            立即发送
          </Button>
        }
        message="确认立即发送这封邮件？邮箱有余量且任务未暂停时就会发出。"
        action="立即发送"
        onConfirm={() => action.mutate()}
      />
      {open && (
        <DateTimeDialog
          initial={initial}
          onClose={() => setOpen(false)}
          onConfirm={(at) => reschedule.mutate(at)}
          pending={reschedule.isPending}
          error={reschedule.error}
        />
      )}
      {action.error && <p className="w-full text-sm text-destructive">{errorMessage(action.error)}</p>}
    </>
  );
}
