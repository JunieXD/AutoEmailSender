import {
  keepPreviousData,
  type UseQueryResult,
  useIsMutating,
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { cn } from "cn";
import { ChevronLeft, RefreshCw, UserRound } from "lucide-react";
import { call, errorMessage } from "@/api/call";
import type { TodoPage } from "@/api/types";
import { Correspondence } from "@/components/correspondence";
import { Avatar, ContactLabel, placeOf } from "@/components/mentor";
import { Pager, pageParam, useLastPage } from "@/components/mentor-table";
import { Notice } from "@/components/notice";
import { TodoCheck, TodoCheckError, dueLabel, dueTone } from "@/components/todos";
import { Button, buttonVariants } from "@/components/ui/button";
import { day, time } from "@/format";
import { useNow } from "@/now";
import { Segments } from "@/components/ui/segments";

export type ConversationSearch = { show?: "unread" | "todos"; page?: number; mentor?: number };

export function conversationSearch(search: Record<string, unknown>): ConversationSearch {
  return {
    show: search.show === "unread" || search.show === "todos" ? search.show : undefined,
    page: pageParam(search.page),
    mentor:
      typeof search.mentor === "number" && Number.isSafeInteger(search.mentor) && search.mentor > 0
        ? search.mentor
        : undefined,
  };
}

/** Receiving now and when mail was last received, the same here and in the background panel. */
export function useReceive() {
  const queryClient = useQueryClient();
  const status = useQuery({
    queryKey: ["receiveStatus"],
    queryFn: () => call("receiveStatus", {}),
    refetchInterval: 60_000,
  });
  const mutation = useMutation({
    mutationKey: ["receiveNow"],
    mutationFn: () => call("receiveNow", {}),
    onSuccess: async (received) => {
      await queryClient.cancelQueries({ queryKey: ["receiveStatus"] });
      queryClient.setQueryData(["receiveStatus"], received);
      for (const key of ["listConversations", "unreadCount", "listMailboxes"]) {
        void queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
  });
  // From the cache, so that receiving still under way, or how it ended, shows after leaving the
  // page and coming back.
  const receiving = useIsMutating({ mutationKey: ["receiveNow"] }) > 0;
  const receiveError = useMutationState({
    filters: { mutationKey: ["receiveNow"] },
    select: (m) => m.state.error,
  }).at(-1);
  const now = useNow();
  const at = status.data?.receivedAt;
  const minutes = at == null ? 0 : Math.max(0, Math.floor((now - at) / 60_000));
  return {
    status,
    receive: () => mutation.mutate(),
    receiving,
    receiveError,
    received:
      at == null ? null : minutes < 1 ? "刚刚收信" : minutes < 60 ? `${minutes} 分钟前收信` : `${time(at)} 收信`,
  };
}
export function ConversationsPage() {
  const search = useSearch({ from: "/conversations" });
  const navigate = useNavigate({ from: "/conversations" });
  const page = search.page ?? 1;
  const unread = search.show === "unread";
  const todos = search.show === "todos";
  const query = useQuery({
    queryKey: ["listConversations", { unread, page }],
    queryFn: () => call("listConversations", { unread, page }),
    placeholderData: keepPreviousData,
    enabled: !todos,
  });
  // Page 1 also gives the count beside the tab.
  const todoQuery = useQuery({
    queryKey: ["listTodos", todos ? page : 1],
    queryFn: () => call("listTodos", { page: todos ? page : 1 }),
    placeholderData: keepPreviousData,
  });
  const listed = todos ? todoQuery : query;
  useLastPage(listed.isSuccess && !listed.isFetching ? listed.data.total : undefined, page, (n) =>
    navigate({ search: (s) => ({ ...s, page: n > 1 ? n : undefined }), replace: true }),
  );
  const todoCount = todoQuery.data?.total;
  const mailboxes = useQuery({ queryKey: ["listMailboxes"], queryFn: () => call("listMailboxes", {}) });
  const { status, receive, receiving, receiveError, received } = useReceive();
  return (
    <div className="grid min-h-0 flex-1 grid-cols-[320px_minmax(0,1fr)] phone:grid-cols-1">
      <section
        className={`flex min-h-0 flex-col border-r border-border phone:border-0 ${search.mentor ? "phone:hidden" : ""}`}
      >
        <header className="px-4 pt-6 pb-3 phone:pt-3.5">
          <div className="flex items-start justify-between gap-2">
            <h1 className="text-2xl font-semibold phone:text-[22px]">往来</h1>
            <div className="flex shrink-0 flex-col items-end gap-1">
              <Button variant="ghost" className="px-2" disabled={receiving} onClick={receive}>
                <RefreshCw strokeWidth={1.6} className={receiving ? "animate-spin" : ""} />
                立即收信
              </Button>
              {received && <p className="text-xs text-muted-foreground">{received}</p>}
            </div>
          </div>
          <Segments
            label="往来筛选"
            className="mt-3"
            value={search.show}
            options={[
              [undefined, "全部"],
              ["unread", "新回信"],
              [
                "todos",
                <>
                  待办
                  {todoCount !== undefined && todoCount > 0 && (
                    <span className="min-w-5 rounded-full bg-warning-soft px-1.5 text-center text-xs leading-5 font-medium text-warning">
                      {todoCount > 99 ? "99+" : todoCount}
                    </span>
                  )}
                </>,
              ],
            ]}
            onChange={(show) => navigate({ search: (s) => ({ ...s, show, page: undefined }) })}
          />
          {(receiveError || status.error || mailboxes.error) && (
            <p className="mt-2 text-sm text-destructive">
              {errorMessage(receiveError ?? status.error ?? mailboxes.error)}
            </p>
          )}
          {mailboxes.data
            ?.filter((m) => m.receiveError !== null)
            .map((m) => (
              <p key={m.id} className="mt-2 text-sm wrap-anywhere text-warning">
                {m.address}收信失败：{m.receiveError}
                <Link to="/settings/mailboxes" className="ml-1 underline">
                  邮箱设置
                </Link>
              </p>
            ))}
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-2">
          {todos ? (
            <TodoList query={todoQuery} selected={search.mentor} />
          ) : query.isError ? (
            <Notice title="往来列表没能加载" detail={errorMessage(query.error)} />
          ) : query.data?.total === 0 ? (
            <Notice
              title={unread ? "没有新回信。" : "还没有往来。"}
              detail={unread ? "" : "给导师发信或收到回信后会出现在这里。"}
            />
          ) : (
            <div className="flex flex-col gap-[3px]">
              {query.data?.conversations.map((c) => (
                <Link
                  key={c.mentorId}
                  to="/conversations"
                  search={(s) => ({ ...s, mentor: c.mentorId })}
                  aria-current={search.mentor === c.mentorId ? "true" : undefined}
                  className={`flex items-center gap-3 rounded-md px-3 py-3 ${search.mentor === c.mentorId ? "bg-selected" : "hover:bg-muted"}`}
                >
                  <Avatar id={c.mentorId} name={c.name} />
                  <div className="min-w-0 grow">
                    <div className="flex items-center justify-between gap-2">
                      <span className={`truncate ${c.unread > 0 ? "font-semibold" : "font-medium"}`}>{c.name}</span>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {day(c.at) === "今天" ? time(c.at) : day(c.at)}
                      </span>
                    </div>
                    <div className="mt-1 flex items-center gap-2">
                      <p className="min-w-0 grow truncate text-sm text-muted-foreground">{c.subject ?? "（无主题）"}</p>
                      {c.unread > 0 && (
                        <span className="min-w-5 shrink-0 rounded-full bg-unread px-1.5 text-center text-xs leading-5 text-white">
                          {c.unread > 99 ? "99+" : c.unread}
                        </span>
                      )}
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>
        <footer className="flex justify-end px-3 py-3 text-xs text-muted-foreground">
          <Pager
            page={page}
            total={listed.data?.total ?? 0}
            onPage={(n) => navigate({ search: (s) => ({ ...s, page: n > 1 ? n : undefined }) })}
          />
        </footer>
      </section>
      <section className={`flex min-h-0 min-w-0 flex-col ${search.mentor ? "" : "phone:hidden"}`}>
        {search.mentor ? (
          <ConversationDetail key={search.mentor} id={search.mentor} />
        ) : (
          <Notice title="选择左边的一位导师查看往来。" detail="" />
        )}
      </section>
    </div>
  );
}

/** Every open todo, overdue first, then by due time, those without one last (docs/ui.md#交互). */
function TodoList({ query, selected }: { query: UseQueryResult<TodoPage>; selected?: number }) {
  const now = useNow();
  if (query.isError) return <Notice title="待办没能加载" detail={errorMessage(query.error)} />;
  if (query.data?.total === 0) {
    return <Notice title="没有要做的事。" detail="导师回信里要你做的事、你自己添加的待办会列在这里。" />;
  }
  return (
    <ul className="flex flex-col gap-[3px]">
      {query.data?.todos.map((t) => {
        const due = dueLabel(t);
        return (
          <li
            key={t.id}
            className={`flex items-start gap-3 rounded-md px-3 py-3 ${selected === t.mentorId ? "bg-selected" : "hover:bg-muted"}`}
          >
            <TodoCheck todo={t} />
            <div className="min-w-0 grow">
              <Link to="/conversations" search={(s) => ({ ...s, mentor: t.mentorId })} className="block">
                <p className="wrap-anywhere">{t.text}</p>
                <p className="mt-1 flex justify-between gap-2 text-sm text-muted-foreground">
                  <span className="truncate">{t.mentorName}</span>
                  {due && <span className={`shrink-0 ${dueTone(t, false, now)}`}>{due}</span>}
                </p>
              </Link>
              <TodoCheckError id={t.id} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function ConversationDetail({ id }: { id: number }) {
  const query = useQuery({ queryKey: ["getMentor", id], queryFn: () => call("getMentor", { id }) });
  const back = (
    <Link
      to="/conversations"
      search={(s) => ({ ...s, mentor: undefined })}
      aria-label="返回往来列表"
      className={cn(buttonVariants({ variant: "ghost", size: "icon" }), "hidden phone:inline-flex")}
    >
      <ChevronLeft strokeWidth={1.6} />
    </Link>
  );
  if (query.isError)
    return (
      <>
        {back}
        <Notice title="导师资料未能加载" detail={errorMessage(query.error)} />
      </>
    );
  if (!query.data) return null;
  const m = query.data;
  const main = m.affiliations.at(0);
  return (
    <>
      <header className="flex items-center gap-3 border-b border-border px-5 py-3.5 phone:px-3">
        {back}
        <Avatar id={m.id} name={m.name} />
        <div className="min-w-0 grow">
          <h2 className="truncate text-lg font-semibold">{m.name}</h2>
          <p className="truncate text-sm text-muted-foreground">{main && placeOf(main)}</p>
        </div>
        <ContactLabel contact={m.contact} className="shrink-0 text-sm phone:hidden" />
        <Link
          to="/mentors/$mentorId"
          params={{ mentorId: id }}
          className={buttonVariants({ variant: "ghost", className: "shrink-0 px-3" })}
        >
          <UserRound strokeWidth={1.6} />
          资料
        </Link>
      </header>
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <Correspondence mentor={m} />
      </div>
    </>
  );
}
