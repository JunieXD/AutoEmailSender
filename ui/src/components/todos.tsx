import { useMutation, useMutationState, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { CalendarDays, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { Todo } from "@/api/types";
import { Confirm } from "@/components/batch-bar";
import { localDate } from "@/components/schedule";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { composing } from "@/composing";

type Due = Pick<Todo, "dueDate" | "dueTime">;

/** After a todo was added, changed or deleted, here or by the core. */
export function refreshTodos(queryClient: QueryClient) {
  for (const queryKey of [["listTodos"], ["listCorrespondence"]]) {
    void queryClient.invalidateQueries({ queryKey });
  }
}

function parseDate(date: string): Date {
  const [y, m, d] = date.split("-").map(Number);
  return new Date(y ?? 0, (m ?? 1) - 1, d ?? 1);
}

/** "10月31日前", "10月31日 17:00 前" (docs/ui.md#交互). */
export function dueLabel({ dueDate, dueTime }: Due): string | null {
  if (!dueDate) return null;
  return `${dayLabel(dueDate)}${dueTime ? ` ${dueTime} ` : ""}前`;
}

function dayLabel(date: string): string {
  const d = parseDate(date);
  const md = `${d.getMonth() + 1}月${d.getDate()}日`;
  return d.getFullYear() === new Date().getFullYear() ? md : `${d.getFullYear()}年${md}`;
}

/** Red once past due, yellow when due today or tomorrow. */
export function dueTone({ dueDate, dueTime }: Due, done: boolean, now: number): string {
  if (!dueDate || done) return "text-muted-foreground";
  const end = parseDate(dueDate);
  if (dueTime) {
    const [h, m] = dueTime.split(":").map(Number);
    end.setHours(h ?? 0, m ?? 0);
  } else {
    end.setDate(end.getDate() + 1);
  }
  if (end.getTime() <= now) return "text-destructive";
  const soon = new Date(now);
  soon.setDate(soon.getDate() + 1);
  return dueDate <= localDate(soon) ? "text-warning" : "text-muted-foreground";
}

export function TodoCheck({ todo }: { todo: Todo }) {
  const queryClient = useQueryClient();
  const set = useMutation({
    mutationKey: ["setTodoDone", todo.id],
    mutationFn: (done: boolean) => call("setTodoDone", { id: todo.id, done }),
    onSettled: () => refreshTodos(queryClient),
  });
  return (
    <Checkbox
      aria-label={todo.done ? `标为没完成：${todo.text}` : `标为完成：${todo.text}`}
      checked={set.isPending ? set.variables : todo.done}
      disabled={set.isPending}
      onCheckedChange={(done) => set.mutate(done)}
      className="mt-[3px]"
    />
  );
}

/** Why ticking a todo last failed, under its text; ticking it again retries. */
export function TodoCheckError({ id }: { id: number }) {
  const error = useMutationState({
    filters: { mutationKey: ["setTodoDone", id] },
    select: (m) => m.state.error,
  }).at(-1);
  return error == null ? null : <p className="mt-1 text-sm text-destructive">未能保存：{errorMessage(error)}</p>;
}

/** One todo in a mentor's correspondence: tick it, or click its text to change or delete it. */
export function TodoItem({ todo, readOnly, now }: { todo: Todo; readOnly: boolean; now: number }) {
  const [editing, setEditing] = useState(false);
  const queryClient = useQueryClient();
  const update = useMutation({
    mutationFn: (t: Pick<Todo, "text" | "dueDate" | "dueTime">) => call("updateTodo", { id: todo.id, ...t }),
    onSuccess: () => setEditing(false),
    onSettled: () => refreshTodos(queryClient),
  });
  const remove = useMutation({
    mutationFn: () => call("deleteTodo", { id: todo.id }),
    onSettled: () => refreshTodos(queryClient),
  });
  if (editing && !readOnly) {
    return (
      <TodoEditor
        initial={todo}
        pending={update.isPending || remove.isPending}
        error={update.error ?? remove.error}
        onSave={(t) => update.mutate(t)}
        onCancel={() => {
          update.reset();
          setEditing(false);
        }}
        onDelete={() => remove.mutate()}
      />
    );
  }
  const due = dueLabel(todo);
  return (
    <div className="flex items-start gap-2.5 px-3 py-2">
      {readOnly ? <Checkbox checked={todo.done} disabled className="mt-[3px]" /> : <TodoCheck todo={todo} />}
      <div className="min-w-0 grow">
        <button
          type="button"
          disabled={readOnly}
          onClick={() => setEditing(true)}
          className={`w-full text-left wrap-anywhere ${todo.done ? "text-faint line-through" : ""}`}
        >
          {todo.text}
        </button>
        <TodoCheckError id={todo.id} />
      </div>
      {due && <span className={`shrink-0 text-sm ${dueTone(todo, todo.done, now)}`}>{due}</span>}
    </div>
  );
}

/** Text and an optional due date and time; the time needs the date. */
export function TodoEditor({
  initial,
  pending,
  error,
  onSave,
  onCancel,
  onDelete,
}: {
  initial?: Pick<Todo, "text" | "dueDate" | "dueTime">;
  pending: boolean;
  error: unknown;
  onSave: (todo: Pick<Todo, "text" | "dueDate" | "dueTime">) => void;
  onCancel: () => void;
  onDelete?: () => void;
}) {
  const [text, setText] = useState(initial?.text ?? "");
  const [dueDate, setDueDate] = useState(initial?.dueDate ?? null);
  const [dueTime, setDueTime] = useState(initial?.dueTime ?? "");
  const [picking, setPicking] = useState(false);
  const form = useRef<HTMLFormElement>(null);
  // Opened at the foot of a long correspondence, its buttons may start below the fold.
  useEffect(() => {
    form.current?.scrollIntoView({ block: "nearest" });
  }, []);
  const valid = text.trim() !== "";
  const save = () => valid && onSave({ text: text.trim(), dueDate, dueTime: dueDate && dueTime ? dueTime : null });
  return (
    <form
      ref={form}
      className="flex flex-col gap-2 px-3 py-2.5"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <Input
        autoFocus
        disabled={pending}
        aria-label="待办内容"
        placeholder="要做的事，比如把成绩单发给王老师"
        maxLength={100}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape" && !composing(e)) onCancel();
        }}
      />
      <div className="flex flex-wrap items-center gap-2">
        <Popover open={picking} onOpenChange={setPicking}>
          <PopoverTrigger render={<Button type="button" variant="quiet" size="sm" disabled={pending} />}>
            <CalendarDays strokeWidth={1.6} />
            {dueDate ? dayLabel(dueDate) : "截止日期"}
          </PopoverTrigger>
          <PopoverContent align="start" className="w-auto p-2">
            <Calendar
              className="bg-transparent p-0"
              mode="single"
              selected={dueDate ? parseDate(dueDate) : undefined}
              onSelect={(d) => {
                setDueDate(d ? localDate(d) : null);
                if (!d) setDueTime("");
                setPicking(false);
              }}
            />
            {dueDate && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="mt-1 w-full"
                onClick={() => {
                  setDueDate(null);
                  setDueTime("");
                  setPicking(false);
                }}
              >
                不设截止日期
              </Button>
            )}
          </PopoverContent>
        </Popover>
        {dueDate && (
          <Input
            type="time"
            disabled={pending}
            aria-label="截止时间"
            className="h-8 w-28"
            value={dueTime}
            onChange={(e) => setDueTime(e.target.value)}
          />
        )}
        <span className="grow" />
        {onDelete && (
          <Confirm
            trigger={
              <Button type="button" variant="ghost" size="sm" className="text-destructive" disabled={pending}>
                删除
              </Button>
            }
            message="删除这条待办？"
            action="删除"
            onConfirm={onDelete}
          />
        )}
        <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={onCancel}>
          取消
        </Button>
        <Button type="submit" size="sm" disabled={pending || !valid}>
          保存
        </Button>
      </div>
      {error != null && <p className="text-sm text-destructive">未能保存：{errorMessage(error)}</p>}
    </form>
  );
}

/** "添加待办" under a mentor's correspondence, which opens the editor in its place. */
export function AddTodo({ mentorId }: { mentorId: number }) {
  const [adding, setAdding] = useState(false);
  const queryClient = useQueryClient();
  const add = useMutation({
    mutationFn: (t: Pick<Todo, "text" | "dueDate" | "dueTime">) => call("addTodo", { mentorId, ...t }),
    onSuccess: () => setAdding(false),
    onSettled: () => refreshTodos(queryClient),
  });
  if (!adding) {
    return (
      <Button variant="ghost" size="sm" className="self-start" onClick={() => setAdding(true)}>
        <Plus strokeWidth={1.8} />
        添加待办
      </Button>
    );
  }
  return (
    <div className="w-full max-w-[min(42rem,88%)] self-start rounded-lg border border-border">
      <TodoEditor
        pending={add.isPending}
        error={add.error}
        onSave={(t) => add.mutate(t)}
        onCancel={() => {
          add.reset();
          setAdding(false);
        }}
      />
    </div>
  );
}
