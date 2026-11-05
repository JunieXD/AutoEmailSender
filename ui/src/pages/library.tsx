import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { ChevronLeft } from "lucide-react";
import { Fragment, type ReactNode, useMemo, useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { LibraryAddPreview, LibraryAdded, LibraryMentor, LibraryOrg } from "@/api/types";
import { AccountDialog, type AccountMode, AgreeTermsButton, useAccount } from "@/components/account";
import { addedMessage, LibraryAddDialog, needsChoices, refreshLibrary } from "@/components/library";
import { ExternalLink, Missing, placeOf } from "@/components/mentor";
import { Pager, useLastPage } from "@/components/mentor-table";
import { Notice } from "@/components/notice";
import { SearchBox } from "@/components/search-box";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { day } from "@/format";

const LIMIT = 200;
const collator = new Intl.Collator("zh-CN");

/** 找导师 › 导师库 (docs/ui.md#交互): only for a logged-in account, as the server counts what it hands out. */
export function LibraryTab() {
  const account = useAccount();
  const [dialog, setDialog] = useState<AccountMode | null>(null);
  const state = account.data;
  if (account.error) return <Notice title="账号未能读取" detail={errorMessage(account.error)} />;
  if (!state) return null;
  if (state.kind === "loggedIn" && state.termsOutdated) {
    return (
      <Notice title="尚未同意新版条款" detail="同意新版《用户服务协议》和《隐私政策》后，方可使用导师库。">
        <div className="mt-4">
          <AgreeTermsButton />
        </div>
      </Notice>
    );
  }
  if (state.kind === "loggedIn") return <Library />;
  return (
    <Notice
      title="登录后使用导师库"
      detail="导师库是维护者整理的各校导师资料，登录后可以浏览，选好的导师直接添加到导师列表。"
    >
      {state.kind === "expired" && <p className="mt-2 text-sm text-warning">登录已失效，请重新登录。</p>}
      <div className="mt-4 flex justify-center gap-2">
        <Button onClick={() => setDialog("login")}>登录</Button>
        <Button variant="quiet" onClick={() => setDialog("register")}>
          注册
        </Button>
      </div>
      {dialog && (
        <AccountDialog
          mode={dialog}
          email={state.kind === "expired" ? state.email : undefined}
          onClose={() => setDialog(null)}
        />
      )}
    </Notice>
  );
}

function Library() {
  const s = useSearch({ from: "/find" });
  const navigate = useNavigate({ from: "/find" });
  const client = useQueryClient();
  const orgs = useQuery({ queryKey: ["getLibraryOrgs"], queryFn: () => call("getLibraryOrgs", {}) });
  const list = useQuery({
    queryKey: ["listLibraryMentors", s.org, s.q, s.page],
    queryFn: () => call("listLibraryMentors", { org: s.org, q: s.q, page: s.page }),
    placeholderData: keepPreviousData,
  });
  const quota = useQuery({ queryKey: ["getLibraryQuota"], queryFn: () => call("getLibraryQuota", {}) });
  // Ticked records, and whether each was taken before and so does not count toward today's number.
  const [selected, setSelected] = useState<ReadonlyMap<string, boolean>>(new Map());
  const [expanded, setExpanded] = useState<string | null>(null);
  // On a phone the schools take the whole screen while choosing.
  const [picking, setPicking] = useState(false);
  const [choosing, setChoosing] = useState<{ ids: string[]; preview: LibraryAddPreview } | null>(null);
  const [added, setAdded] = useState<LibraryAdded | null>(null);
  const done = (result: LibraryAdded) => {
    setAdded(result);
    setChoosing(null);
    setSelected(new Map());
  };
  const add = useMutation({
    mutationFn: async (ids: string[]) => {
      const preview = await call("previewLibraryAdd", { ids });
      // Reading the records for the preview already used up today's number for them.
      setSelected(new Map(ids.map((id) => [id, true])));
      return needsChoices(preview) ? { ids, preview } : { added: await call("addLibraryMentors", { ids }) };
    },
    onSuccess: (r) => (r.added ? done(r.added) : setChoosing(r)),
    onSettled: () => refreshLibrary(client),
  });
  useLastPage(list.isSuccess && !list.isFetching ? list.data.total : undefined, s.page, (page) =>
    navigate({ search: (old) => ({ ...old, page }), replace: true }),
  );
  const byId = useMemo(() => new Map(orgs.data?.orgs.map((o) => [o.id, o])), [orgs.data]);
  const path = s.org ? pathOf(s.org, byId) : [];
  const items = list.data?.mentors ?? [];
  const counted = [...selected.values()].filter((fetched) => !fetched).length;
  const remaining = quota.data?.remaining;
  // Rows of the previous page stay on screen until the next arrives, and must not be ticked meanwhile.
  const waiting = list.isPlaceholderData;
  const selectable = items.filter((m) => m.local?.kind !== "added" && m.local?.kind !== "trashed");
  const tick = (ms: LibraryMentor[], on: boolean) =>
    setSelected((old) => {
      const next = new Map(old);
      for (const m of ms) {
        if (!on) next.delete(m.id);
        else if (next.size < LIMIT) next.set(m.id, m.fetched);
      }
      return next;
    });
  const choose = (org: string | undefined) => {
    setPicking(false);
    setExpanded(null);
    void navigate({ search: (old) => ({ ...old, org, page: 1 }) });
  };
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-0 flex-1">
        <aside
          className={`flex w-64 shrink-0 flex-col gap-2 overflow-y-auto px-4 pb-6 phone:w-full ${picking ? "" : "phone:hidden"}`}
        >
          {orgs.error ? (
            <Retry error={orgs.error} onRetry={() => void orgs.refetch()} />
          ) : (
            orgs.data && <Orgs orgs={orgs.data.orgs} byId={byId} selected={s.org} onSelect={choose} />
          )}
        </aside>
        <section className={`flex min-w-0 flex-1 flex-col ${picking ? "phone:hidden" : ""}`}>
          <div className="flex shrink-0 flex-wrap items-center gap-3 px-6 pb-3 phone:px-4">
            <Button variant="ghost" className="hidden px-2 phone:inline-flex" onClick={() => setPicking(true)}>
              <ChevronLeft strokeWidth={1.6} />
              {path.at(-1)?.name ?? "全部学校"}
            </Button>
            <SearchBox
              label="搜索导师库"
              placeholder="搜索姓名或研究方向"
              className="w-[320px] phone:w-full"
              value={s.q ?? ""}
              onChange={(q) => {
                setExpanded(null);
                void navigate({ search: (old) => ({ ...old, q: q || undefined, page: 1 }) });
              }}
            />
            {list.data && (
              <span className="text-sm text-muted-foreground">共 {list.data.total.toLocaleString()} 位</span>
            )}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6 phone:px-4">
            {added && (
              <p
                role="status"
                className="mb-3 flex flex-wrap items-center gap-x-3 rounded-md bg-muted px-3 py-2 text-sm"
              >
                {addedMessage(added)}
                <Link to="/mentors" className="text-brand-text">
                  去导师列表
                </Link>
              </p>
            )}
            {list.error && <Retry error={list.error} onRetry={() => void list.refetch()} />}
            {selectable.length > 0 && (
              <label className="flex min-h-10 items-center gap-3 px-3 text-sm">
                <Checkbox
                  disabled={waiting}
                  checked={selectable.every((m) => selected.has(m.id))}
                  onCheckedChange={(checked) => tick(selectable, checked)}
                />
                选择本页
              </label>
            )}
            {list.data?.total === 0 && (
              <p className="py-12 text-center text-muted-foreground">{s.q ? `没有找到“${s.q}”` : "这里还没有导师"}</p>
            )}
            {items.map((m) => (
              <Fragment key={m.id}>
                <Row
                  mentor={m}
                  university={path[0]?.name}
                  selected={selected.has(m.id)}
                  disabled={waiting || (selected.size >= LIMIT && !selected.has(m.id))}
                  expanded={expanded === m.id}
                  onTick={(on) => tick([m], on)}
                  onExpand={() => setExpanded(expanded === m.id ? null : m.id)}
                />
                {expanded === m.id && <Preview id={m.id} />}
              </Fragment>
            ))}
            {list.data && (s.page > 1 || list.data.total > 50) && (
              <div className="mt-3 flex justify-end text-sm text-muted-foreground">
                <Pager
                  page={s.page}
                  total={list.data.total}
                  onPage={(page) => {
                    setExpanded(null);
                    void navigate({ search: (old) => ({ ...old, page }) });
                  }}
                />
              </div>
            )}
            <p className="mt-6 text-xs text-faint">
              服务器会记下你添加过哪些导师，用来计算每天的数量和推送资料更新；你的标签、备注、往来和匹配结果不会上传。
            </p>
          </div>
        </section>
      </div>
      <footer className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-t border-border px-6 py-3 text-sm phone:px-4">
        <span>已选 {selected.size} 位</span>
        {selected.size > 0 && (
          <Button variant="ghost" size="sm" onClick={() => setSelected(new Map())}>
            清空
          </Button>
        )}
        {selected.size >= LIMIT && <span className="text-muted-foreground">一次最多选 {LIMIT} 位</span>}
        <Quota remaining={remaining} counted={counted} />
        <span className="grow" />
        {(quota.error ?? add.error) && (
          <span role="alert" className="text-destructive">
            {errorMessage(quota.error ?? add.error)}
          </span>
        )}
        <Button
          disabled={selected.size === 0 || add.isPending || (remaining !== undefined && counted > remaining)}
          onClick={() => add.mutate([...selected.keys()])}
        >
          {add.isPending ? "正在添加…" : "添加到导师列表"}
        </Button>
      </footer>
      {choosing && (
        <LibraryAddDialog
          ids={choosing.ids}
          initial={choosing.preview}
          onClose={() => setChoosing(null)}
          onDone={(result) => {
            done(result);
            refreshLibrary(client);
          }}
        />
      )}
    </div>
  );
}

function Quota({ remaining, counted }: { remaining: number | undefined; counted: number }) {
  if (remaining === undefined) return null;
  return (
    <span className={counted > remaining ? "text-warning" : "text-muted-foreground"}>
      {remaining === 0
        ? "今天的数量已用完，明天 0 点（北京时间）恢复"
        : counted > remaining
          ? `今天还可添加 ${remaining} 位，已选的有 ${counted} 位要计入数量`
          : `今天还可添加 ${remaining} 位`}
    </span>
  );
}

/** The org and its parents, the school first. */
function pathOf(id: string, byId: ReadonlyMap<string, LibraryOrg>): LibraryOrg[] {
  const path: LibraryOrg[] = [];
  for (let org = byId.get(id); org; org = org.parentId ? byId.get(org.parentId) : undefined) path.unshift(org);
  return path;
}

const DEPTH = ["pl-2", "pl-6", "pl-10"];

function Orgs({
  orgs,
  byId,
  selected,
  onSelect,
}: {
  orgs: LibraryOrg[];
  byId: ReadonlyMap<string, LibraryOrg>;
  selected: string | undefined;
  onSelect: (org: string | undefined) => void;
}) {
  const [filter, setFilter] = useState("");
  const children = useMemo(() => {
    const byParent = new Map<string | null, LibraryOrg[]>();
    for (const o of orgs.toSorted((a, b) => collator.compare(a.name, b.name)))
      byParent.set(o.parentId, [...(byParent.get(o.parentId) ?? []), o]);
    return byParent;
  }, [orgs]);
  // The selected org's parents stay open, and so does the org itself to show what is under it.
  const open = new Set(selected ? pathOf(selected, byId).map((o) => o.id) : []);
  const keyword = filter.trim().toLowerCase();
  const row = (o: LibraryOrg, depth: number, label = o.name) => (
    <button
      key={o.id}
      aria-current={o.id === selected || undefined}
      onClick={() => onSelect(o.id)}
      className={`flex w-full items-baseline gap-2 rounded-md py-1.5 pr-2 text-left text-sm hover:bg-muted aria-current:bg-selected ${DEPTH[depth]}`}
    >
      <span className="min-w-0 grow break-words">{label}</span>
      <span className="shrink-0 text-xs text-muted-foreground">{o.count}</span>
    </button>
  );
  const tree = (parent: string | null, depth: number): ReactNode =>
    children.get(parent)?.map((o) => (
      <Fragment key={o.id}>
        {row(o, depth)}
        {open.has(o.id) && tree(o.id, depth + 1)}
      </Fragment>
    ));
  return (
    <>
      <Input
        aria-label="搜索学校或学院"
        placeholder="搜索学校或学院"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      <nav aria-label="学校和学院">
        {keyword ? (
          orgs
            .filter((o) => o.name.toLowerCase().includes(keyword))
            .toSorted((a, b) => collator.compare(a.name, b.name))
            .map((o) =>
              row(
                o,
                0,
                pathOf(o.id, byId)
                  .map((p) => p.name)
                  .join(" · "),
              ),
            )
        ) : (
          <>
            <button
              aria-current={selected === undefined || undefined}
              onClick={() => onSelect(undefined)}
              className="w-full rounded-md py-1.5 pr-2 pl-2 text-left text-sm hover:bg-muted aria-current:bg-selected"
            >
              全部学校
            </button>
            {tree(null, 0)}
          </>
        )}
      </nav>
    </>
  );
}

function Row({
  mentor: m,
  university,
  selected,
  disabled,
  expanded,
  onTick,
  onExpand,
}: {
  mentor: LibraryMentor;
  /** The school chosen on the left, which goes without saying on each row. */
  university: string | undefined;
  selected: boolean;
  disabled: boolean;
  expanded: boolean;
  onTick: (on: boolean) => void;
  onExpand: () => void;
}) {
  const local = m.local;
  const blocked = local?.kind === "added" || local?.kind === "trashed";
  return (
    <div
      data-selected={selected || undefined}
      className="flex items-start gap-1 rounded-md px-1 hover:bg-muted data-selected:bg-selected"
    >
      <label className="flex min-h-10 min-w-10 cursor-pointer items-center justify-center pt-1">
        <Checkbox
          aria-label={`选择${m.name}`}
          checked={selected}
          disabled={blocked || disabled}
          onCheckedChange={onTick}
        />
      </label>
      <button className="min-w-0 flex-1 py-2.5 pr-2 text-left" aria-expanded={expanded} onClick={onExpand}>
        <span className="flex flex-wrap items-baseline gap-x-2">
          <strong className="font-medium">{m.name}</strong>
          {local?.kind === "added" && <span className="text-xs text-muted-foreground">已添加</span>}
          {local?.kind === "trashed" && <span className="text-xs text-muted-foreground">在回收站</span>}
          {local?.kind === "maybeExisting" && (
            <span className="text-xs text-warning" title={`导师列表里有同名、同校的${local.name}`}>
              可能已有
            </span>
          )}
          {m.emailCount === 0 && <span className="text-xs text-warning">没有邮箱</span>}
        </span>
        <span className="mt-0.5 block truncate text-sm text-muted-foreground">
          {[m.university !== university && m.university, m.school, m.department, m.title].filter(Boolean).join(" · ")}
        </span>
        {m.research && <span className="mt-0.5 block truncate text-sm text-muted-foreground">{m.research}</span>}
      </button>
    </div>
  );
}

function Preview({ id }: { id: string }) {
  const preview = useQuery({
    queryKey: ["previewLibraryMentor", id],
    queryFn: () => call("previewLibraryMentor", { id }),
  });
  if (preview.error) return <Retry error={preview.error} onRetry={() => void preview.refetch()} />;
  const p = preview.data;
  if (!p) return null;
  return (
    <dl className="mb-2 ml-11 grid grid-cols-[64px_minmax(0,1fr)] gap-x-3 gap-y-2 rounded-lg bg-muted p-4 text-sm [&_dd]:wrap-anywhere [&_dt]:text-muted-foreground">
      {p.aliases.length > 0 && (
        <>
          <dt>别名</dt>
          <dd>{p.aliases.join("、")}</dd>
        </>
      )}
      <dt>单位</dt>
      <dd>
        {p.units.length === 0 && <Missing>未填写</Missing>}
        {p.units.map((u, n) => (
          <div key={n}>{[placeOf(u), u.title].filter(Boolean).join("，")}</div>
        ))}
      </dd>
      <dt>邮箱</dt>
      <dd>{p.emailCount > 0 ? `有 ${p.emailCount} 个邮箱，添加后可见` : <Missing>没有邮箱</Missing>}</dd>
      <dt>研究方向</dt>
      <dd className="whitespace-pre-wrap">{p.research ?? <Missing>未填写</Missing>}</dd>
      {p.papers.length > 0 && (
        <>
          <dt>代表论文</dt>
          <dd>
            <ol className="flex list-decimal flex-col gap-1 pl-[1.1em]">
              {p.papers.map((paper) => (
                <li key={paper}>{paper}</li>
              ))}
            </ol>
          </dd>
        </>
      )}
      {p.homepages.length > 0 && (
        <>
          <dt>主页</dt>
          <dd>
            {p.homepages.map((url) => (
              <div key={url}>
                <ExternalLink url={url} />
              </div>
            ))}
          </dd>
        </>
      )}
      {p.sourceUrl && (
        <>
          <dt>来源</dt>
          <dd>
            <ExternalLink url={p.sourceUrl} />
          </dd>
        </>
      )}
      {p.verifiedAt !== null && (
        <>
          <dt>核实</dt>
          <dd>{day(p.verifiedAt)}</dd>
        </>
      )}
    </dl>
  );
}

function Retry({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <p role="alert" className="flex flex-wrap items-center gap-x-2 py-2 text-sm text-destructive">
      {errorMessage(error)}
      <Button variant="ghost" size="sm" onClick={onRetry}>
        重试
      </Button>
    </p>
  );
}
