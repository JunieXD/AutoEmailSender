import { skipToken, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { openUrl } from "@tauri-apps/plugin-opener";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { CandidateFilter, CandidateRow, CrawlSummary, Decisions, EnrichPlan } from "@/api/types";
import { ModelAction, AiIcon } from "@/components/ai-writing";
import { BatchBar, Confirm } from "@/components/batch-bar";
import { IntakeChoices, ProfileDetails } from "@/components/intake-choices";
import { refreshMentors, placeOf } from "@/components/mentor";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { NativeSelect } from "@/components/ui/native-select";
import { day, time } from "@/format";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";

const FILTERS = {
  all: "全部",
  new: "新导师",
  supplement: "可补充已有导师",
  maybe: "可能是同一位",
  missing_email: "缺邮箱",
} as const;
const EMPTY: Decisions = { take: [], matches: [], restore: [] };
const STATES: Record<string, string> = {
  running: "抓取中",
  paused: "已暂停",
  done: "已完成",
  canceled: "已取消",
  failed: "失败",
  lists: "寻找名单",
  profiles: "读取个人页",
  enrich: "信息补全",
};
export function findSearch(s: Record<string, unknown>) {
  const filter: CandidateFilter =
    s.filter === "new" || s.filter === "supplement" || s.filter === "maybe" || s.filter === "missing_email"
      ? s.filter
      : "all";
  return {
    tab: s.tab === "crawl" ? ("crawl" as const) : ("candidates" as const),
    filter,
    crawl: Number(s.crawl) || undefined,
    processed: s.processed === true,
    page: Math.max(1, Number(s.page) || 1),
    detail: Number(s.detail) || undefined,
  };
}
export function refreshCrawls(client: ReturnType<typeof useQueryClient>) {
  for (const key of [
    "listCrawls",
    "crawlOptions",
    "crawlWork",
    "getCrawl",
    "listCandidates",
    "getCandidate",
    "previewCandidates",
    "previewEnrich",
  ])
    void client.invalidateQueries({ queryKey: [key] });
}
export function FindPage() {
  const search = useSearch({ from: "/find" });
  const navigate = useNavigate({ from: "/find" });
  return (
    <>
      <header className="shrink-0 px-6 pt-6 phone:px-4">
        <h1 className="text-2xl font-semibold">找导师</h1>
        <div className="my-4 flex w-fit gap-1 rounded-full bg-muted p-1" role="tablist" aria-label="找导师">
          {(["candidates", "crawl"] as const).map((tab) => (
            <Button
              key={tab}
              role="tab"
              aria-selected={search.tab === tab}
              variant={search.tab === tab ? "quiet" : "ghost"}
              onClick={() => void navigate({ search: (s) => ({ ...s, tab, page: 1 }) })}
            >
              {tab === "candidates" ? "候选" : "智能抓取"}
            </Button>
          ))}
        </div>
      </header>
      {search.tab === "crawl" ? <Crawls /> : <Candidates />}
    </>
  );
}
function Crawls() {
  const s = useSearch({ from: "/find" });
  const navigate = useNavigate({ from: "/find" });
  const client = useQueryClient();
  const { data: urls = "" } = useQuery({
    queryKey: ["crawlUrls"],
    queryFn: skipToken,
    initialData: "",
    gcTime: Infinity,
  });
  const setUrls = (value: string) => client.setQueryData(["crawlUrls"], value);
  const validation = useQuery({
    queryKey: ["checkCrawlUrls", urls],
    queryFn: () => call("checkCrawlUrls", { urls }),
    retry: false,
  });
  const models = useQuery({ queryKey: ["listModelConfigs"], queryFn: () => call("listModelConfigs", {}) });
  const chosen = models.data?.find((m) => m.isDefault);
  const cost =
    chosen && (chosen.inputPrice !== null || chosen.outputPrice !== null)
      ? ((chosen.inputPrice ?? 0) * 4000 + (chosen.outputPrice ?? 0) * 1000) / 1_000_000
      : null;
  const records = useQuery({
    queryKey: ["listCrawls", s.page],
    queryFn: () => call("listCrawls", { page: s.page }),
    refetchInterval: 2000,
  });
  const start = useMutation({
    mutationFn: () => {
      if (!chosen) throw new Error("请在设置中添加模型");
      return call("startCrawl", { urls, modelId: chosen.id });
    },
    onError: () => client.invalidateQueries({ queryKey: ["listModelConfigs"] }),
    onSuccess: (r) => {
      setUrls("");
      refreshCrawls(client);
      void navigate({ search: (old) => ({ ...old, page: 1, detail: r.id }) });
    },
  });
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 phone:px-4">
      <section className="mb-6 max-w-3xl space-y-3 rounded-lg bg-muted p-4">
        <label className="block font-medium" htmlFor="crawl-urls">
          学院官网或教师名单网址
        </label>
        <Textarea
          id="crawl-urls"
          rows={3}
          value={urls}
          placeholder="每行一个网址，也可以填写一位导师的个人页"
          onChange={(e) => setUrls(e.target.value)}
          aria-describedby="crawl-usage crawl-url-error"
          aria-invalid={!!urls.trim() && validation.isError}
        />
        <p id="crawl-usage" className="text-sm text-muted-foreground">
          一位导师约 5000 Token{cost !== null && `，按已填单价约 ¥${cost.toFixed(3)}／位`}
          。实际用量随人数和网页长度变化，另计寻找名单的用量。
        </p>
        <p className="text-xs text-muted-foreground">
          自动寻找名单和分类，读取个人页后进入候选审核。网页标题、文字、链接文字和公开的导师资料会发给模型服务商。
        </p>
        <ModelAction
          disabled={start.isPending || !validation.data || validation.isFetching}
          modelId={chosen?.id}
          onWrite={() => start.mutate()}
        >
          <AiIcon />
          {start.isPending ? "正在开始…" : "开始抓取"}
        </ModelAction>
        <div id="crawl-url-error" className="text-sm text-muted-foreground">
          {!urls.trim()
            ? "请填写至少一个学院官网或教师名单网址"
            : validation.error
              ? errorMessage(validation.error)
              : validation.isFetching
                ? "正在检查网址…"
                : null}
        </div>
        <ErrorText error={models.error ?? start.error} />
      </section>
      {s.detail && <OpenedCrawl key={s.detail} id={s.detail} />}
      <h2 className="mb-3 text-lg font-medium">抓取记录</h2>
      <ErrorText error={records.error} />
      {records.data?.length === 0 && <p className="text-muted-foreground">尚无抓取记录，从上方填写网址开始</p>}
      <div className="space-y-3">
        {records.data
          ?.filter((c) => c.id !== s.detail)
          .map((c) => (
            <CrawlRecord key={c.id} crawl={c} expanded={false} />
          ))}
      </div>
      <Pager
        page={s.page}
        more={records.data?.length === 50}
        onPage={(page) => void navigate({ search: (old) => ({ ...old, page }) })}
      />
    </div>
  );
}
function OpenedCrawl({ id }: { id: number }) {
  const detail = useQuery({
    queryKey: ["getCrawl", id, 1],
    queryFn: () => call("getCrawl", { id, page: 1 }),
    refetchInterval: 2000,
  });
  return (
    <div className="mb-6">
      <ErrorText error={detail.error} />
      {detail.data && <CrawlRecord crawl={detail.data.summary} expanded />}
    </div>
  );
}
function CrawlRecord({ crawl: c, expanded }: { crawl: CrawlSummary; expanded: boolean }) {
  const navigate = useNavigate({ from: "/find" });
  return (
    <section className="rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-center gap-3">
        <button
          className="mr-auto min-h-10 min-w-0 text-left"
          onClick={() => void navigate({ search: (s) => ({ ...s, detail: expanded ? undefined : c.id }) })}
          aria-expanded={expanded}
        >
          <strong className="break-all">{c.url ?? `抓取 #${c.id}`}</strong>
          <span className="ml-3 text-sm text-muted-foreground">
            {c.state === "running" && c.stage === "enrich" ? "补全中" : STATES[c.state]}
          </span>
          <span className="mt-1 block text-sm text-muted-foreground">
            {STATES[c.stage]} · 已处理 {c.pages} 页 · 已读 {c.profiles} 个个人页 · {c.candidates} 位候选
            {c.remaining > 0 && ` · 待处理 ${c.remaining} 页`}
            {c.enrichRemaining > 0 && ` · 信息补全还剩 ${c.enrichRemaining} 位`}
          </span>
        </button>
        <CrawlControls crawl={c} />
        <Button
          variant="ghost"
          onClick={() =>
            void navigate({ search: (s) => ({ ...s, tab: "candidates", crawl: c.id, page: 1, processed: false }) })
          }
        >
          查看候选
        </Button>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        {day(c.createdAt)} {time(c.createdAt)} ·{" "}
        {c.tokens === null ? "服务商尚未报告用量" : `已用 ${c.tokens.toLocaleString()} Token`}
      </p>
      {c.error && <p className="mt-2 text-sm text-warning">{c.error}</p>}
      {c.failed > 0 && <p className="mt-2 text-sm text-warning">名单可能不完整：{c.failed} 个页面未打开或需要浏览器</p>}
      {expanded && <CrawlDetails id={c.id} modelId={c.modelConfigId} modelIds={c.modelIds} />}
    </section>
  );
}
function CrawlControls({ crawl: c }: { crawl: CrawlSummary }) {
  const client = useQueryClient();
  const mutation = useMutation({
    mutationFn: (action: "pauseCrawl" | "resumeCrawl" | "cancelCrawl") => call(action, { id: c.id }),
    onSuccess: () => refreshCrawls(client),
    onError: () => client.invalidateQueries({ queryKey: ["listModelConfigs"] }),
  });
  return (
    <div className="flex flex-wrap gap-1">
      {c.state === "running" && (
        <Button variant="quiet" disabled={mutation.isPending} onClick={() => mutation.mutate("pauseCrawl")}>
          暂停
        </Button>
      )}
      {c.state === "paused" && (
        <ModelAction disabled={mutation.isPending} modelIds={c.modelIds} onWrite={() => mutation.mutate("resumeCrawl")}>
          继续
        </ModelAction>
      )}
      {(c.state === "running" || c.state === "paused") && (
        <Confirm
          trigger={
            <Button variant="ghost" disabled={mutation.isPending}>
              取消工作
            </Button>
          }
          message="取消后保留已找到的候选和已补到的资料，仍可审核"
          action="取消工作"
          onConfirm={() => mutation.mutate("cancelCrawl")}
        />
      )}
      <ErrorText error={mutation.error} />
    </div>
  );
}
function CrawlDetails({ id, modelId, modelIds }: { id: number; modelId: number | null; modelIds: number[] }) {
  const client = useQueryClient();
  const [page, setPage] = useState(1);
  const detail = useQuery({
    queryKey: ["getCrawl", id, page],
    queryFn: () => call("getCrawl", { id, page }),
    refetchInterval: 2000,
  });
  const retry = useMutation({
    mutationFn: (target: number | null) => call("retryCrawl", { id, page: target }),
    onSuccess: () => refreshCrawls(client),
    onError: () => client.invalidateQueries({ queryKey: ["listModelConfigs"] }),
  });
  return (
    <div className="mt-4 space-y-3 border-t border-border pt-4 text-sm">
      <ErrorText error={detail.error ?? retry.error} />
      {detail.data && (
        <>
          <div className="space-y-1 break-all">
            {detail.data.urls.map((url) => (
              <p key={url}>{url}</p>
            ))}
          </div>
          {detail.data.limits.length > 0 && (
            <div className="rounded-md bg-warning-soft p-3 text-warning">
              名单可能不完整：{detail.data.limits.join("；")}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="mr-auto font-medium">页面及未打开的原因</h3>
            <ModelAction modelIds={modelIds} disabled={retry.isPending} onWrite={() => retry.mutate(null)}>
              重试全部未打开页面
            </ModelAction>
          </div>
          <p className="text-xs text-muted-foreground">
            需要浏览器验证、渲染、解密或点击翻页的页面保留已读资料；本次无法接管浏览器
          </p>
          {detail.data.pages.map((p) => (
            <div key={p.id} className="flex items-start gap-2 rounded-md bg-muted p-3">
              <div className="min-w-0 grow">
                <p className="break-all">{p.url}</p>
                <p
                  className={
                    p.status !== "success" && p.state === "done" ? "mt-1 text-warning" : "mt-1 text-muted-foreground"
                  }
                >
                  {p.state === "queued" ? "待读取" : p.state === "running" ? "正在读取" : (p.error ?? "已读取")}
                </p>
              </div>
              {p.state === "done" && p.status !== "success" && (
                <ModelAction
                  modelId={p.kind.startsWith("enrich_") ? null : modelId}
                  disabled={retry.isPending}
                  onWrite={() => retry.mutate(p.id)}
                >
                  重试
                </ModelAction>
              )}
            </div>
          ))}
          <Pager page={page} more={page * 50 < detail.data.pageTotal} onPage={setPage} />
          <details>
            <summary className="cursor-pointer text-muted-foreground">运行记录</summary>
            {detail.data.runs.map((r, n) => (
              <p key={n} className="mt-2 text-xs text-muted-foreground">
                {day(r.startedAt)} {time(r.startedAt)} — {r.endedAt ? `${day(r.endedAt)} ${time(r.endedAt)}` : "进行中"}{" "}
                · {r.appVersion}
              </p>
            ))}
          </details>
        </>
      )}
    </div>
  );
}
function Candidates() {
  const s = useSearch({ from: "/find" });
  const navigate = useNavigate({ from: "/find" });
  const client = useQueryClient();
  const [selected, setSelected] = useState<number[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [enrich, setEnrich] = useState<number[] | null>(null);
  const [enrichResult, setEnrichResult] = useState<EnrichPlan | null>(null);
  const [review, setReview] = useState<number[] | null>(null);
  const [choices, setChoices] = useState<Record<number, Decisions>>({});
  const q = { crawlId: s.crawl ?? null, filter: s.filter, processed: s.processed, page: s.page };
  const list = useQuery({
    queryKey: ["listCandidates", q],
    queryFn: () => call("listCandidates", q),
    refetchInterval: 3000,
  });
  const crawls = useQuery({
    queryKey: ["crawlOptions"],
    queryFn: () => call("crawlOptions", {}),
  });
  const reject = useMutation({
    mutationFn: (ids: number[]) => call("rejectCandidates", { ids }),
    onSuccess: () => {
      setSelected([]);
      refreshCrawls(client);
    },
  });
  const change = (part: Partial<typeof s>) => {
    setSelected([]);
    void navigate({ search: (old) => ({ ...old, ...part, page: 1 }) });
  };
  const items = list.data?.candidates ?? [];
  return (
    <>
      <div className="shrink-0 space-y-3 px-6 pb-4 phone:px-4">
        <div className="flex flex-wrap items-center gap-2">
          <NativeSelect
            aria-label="按抓取筛选"
            className="max-w-full"
            value={s.crawl ?? ""}
            onChange={(e) => change({ crawl: Number(e.target.value) || undefined })}
          >
            <option value="">全部抓取</option>
            {crawls.data?.map((c) => (
              <option key={c.id} value={c.id}>
                {day(c.createdAt)} {time(c.createdAt)} ·{" "}
                {c.url
                  ? c.url
                      .replace(/^https?:\/\//, "")
                      .replace(/\/$/, "")
                      .slice(0, 48)
                  : "Agent 提交"}
              </option>
            ))}
          </NativeSelect>
          <Button
            variant={s.processed ? "quiet" : "ghost"}
            aria-pressed={s.processed}
            onClick={() => change({ processed: !s.processed })}
          >
            {s.processed ? "返回待审核" : "已处理"}
          </Button>
        </div>
        <div role="group" aria-label="候选分类" className="flex flex-wrap gap-1">
          {Object.entries(FILTERS).map(([value, label]) => (
            <Button
              key={value}
              variant={value === s.filter ? "quiet" : "ghost"}
              aria-pressed={value === s.filter}
              onClick={() => change({ filter: findSearch({ filter: value }).filter })}
            >
              {label}
            </Button>
          ))}
        </div>
        {enrichResult && (
          <p role="status" className="text-sm text-muted-foreground">
            已安排补全 {enrichResult.ids.length} 位<SkippedEnrich plan={enrichResult} />
          </p>
        )}
        <ErrorText error={list.error ?? crawls.error ?? reject.error} />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-24 phone:px-4">
        {!s.processed && items.length > 0 && (
          <label className="mb-3 flex min-h-10 items-center gap-2 text-sm">
            <Checkbox
              checked={items.every((c) => selected.includes(c.id))}
              onCheckedChange={(checked) =>
                setSelected(
                  checked
                    ? [...new Set([...selected, ...items.map((c) => c.id)])]
                    : selected.filter((id) => !items.some((c) => c.id === id)),
                )
              }
            />
            选择本页
          </label>
        )}
        {items.length === 0 && !list.isPending && (
          <p className="py-12 text-center text-muted-foreground">
            {s.processed ? "暂无已处理的候选" : "暂无符合条件的候选，可从智能抓取开始"}
          </p>
        )}
        <div className="space-y-2">
          {items.map((c) => (
            <section key={c.id} className="rounded-lg bg-muted p-4">
              <div className="flex flex-wrap items-start gap-3">
                {!s.processed && (
                  <label className="flex min-h-10 min-w-10 cursor-pointer items-center justify-center">
                    <Checkbox
                      aria-label={`选择${c.name}`}
                      checked={selected.includes(c.id)}
                      onCheckedChange={(checked) =>
                        setSelected((ids) => (checked ? [...ids, c.id] : ids.filter((id) => id !== c.id)))
                      }
                    />
                  </label>
                )}
                <button
                  className="min-h-10 min-w-0 flex-1 text-left"
                  onClick={() => setExpanded(expanded === c.id ? null : c.id)}
                  aria-expanded={expanded === c.id}
                >
                  <strong>{c.name}</strong>
                  <span className="ml-2 text-xs text-muted-foreground">
                    {s.processed ? (c.review === "rejected" ? "已拒绝" : "已接受") : FILTERS[c.category]}
                  </span>
                  <span className="mt-1 block text-sm text-muted-foreground">
                    {c.affiliation && [placeOf(c.affiliation), c.affiliation.title].filter(Boolean).join(" · ")}
                  </span>
                  <span className="mt-1 block text-sm break-all text-muted-foreground">
                    {c.emails.join("、") || "缺邮箱 · 仍可接受，发信前请补充"}
                  </span>
                  {c.research && (
                    <span className="mt-1 block line-clamp-2 text-sm text-muted-foreground">{c.research}</span>
                  )}
                </button>
                {!s.processed && (
                  <div className="flex gap-1 phone:w-full phone:justify-end">
                    <Button size="sm" disabled={!!c.issue} onClick={() => setReview([c.id])}>
                      {c.category === "supplement" ? "补充" : "接受"}
                    </Button>
                    <Button size="sm" variant="ghost" disabled={reject.isPending} onClick={() => reject.mutate([c.id])}>
                      拒绝
                    </Button>
                  </div>
                )}
                {s.processed && c.mentorId && (
                  <Link to="/mentors/$mentorId" params={{ mentorId: c.mentorId }} className="text-sm text-brand-text">
                    查看导师
                  </Link>
                )}
              </div>
              {(c.enrich === "queued" || c.enrich === "running") && (
                <p role="status" className="mt-2 text-sm text-brand-text">
                  补全中
                </p>
              )}
              {c.enrichChanges.length > 0 && (
                <p className="mt-2 text-sm text-brand-text">
                  补到了{[...new Set(c.enrichChanges.map((v) => v.field))].join("、")}
                </p>
              )}
              {c.enrichError && <p className="mt-2 text-sm text-warning">{c.enrichError}</p>}
              {c.sourceUrl && <SourceLink url={c.sourceUrl} />}
              {c.issue && <p className="mt-2 text-sm text-warning">{c.issue}</p>}
              {expanded === c.id && (
                <CandidateDetails
                  candidate={c}
                  decisions={choices[c.id] ?? EMPTY}
                  onChange={(next) =>
                    setChoices((previous) => ({
                      ...previous,
                      [c.id]: typeof next === "function" ? next(previous[c.id] ?? EMPTY) : next,
                    }))
                  }
                />
              )}
            </section>
          ))}
        </div>
        <Pager
          page={s.page}
          more={s.page * 50 < (list.data?.total ?? 0)}
          onPage={(page) => void navigate({ search: (old) => ({ ...old, page }) })}
        />
      </div>
      {!s.processed && (
        <BatchBar count={selected.length} errors={[]} onClear={() => setSelected([])}>
          <Button size="sm" onClick={() => setReview([...selected])}>
            批量接受
          </Button>
          <Button size="sm" variant="quiet" onClick={() => setEnrich([...selected])}>
            补全
          </Button>
          <Button size="sm" variant="ghost" disabled={reject.isPending} onClick={() => reject.mutate(selected)}>
            批量拒绝
          </Button>
        </BatchBar>
      )}
      {enrich && (
        <EnrichDialog
          ids={enrich}
          onClose={() => setEnrich(null)}
          onDone={(result) => {
            setEnrichResult(result);
            setEnrich(null);
            setSelected([]);
            refreshCrawls(client);
          }}
        />
      )}
      {review && (
        <ReviewDialog
          ids={review}
          initialDecisions={review.length === 1 && review[0] !== undefined ? (choices[review[0]] ?? EMPTY) : EMPTY}
          onClose={() => setReview(null)}
          onDone={() => {
            setSelected([]);
            setReview(null);
            refreshCrawls(client);
            refreshMentors(client);
          }}
        />
      )}
    </>
  );
}
const ENRICH_SKIP = {
  missing_profile: "没有个人页",
  complete: "资料已全",
  active: "正在补全",
  reviewed: "已处理或已删除",
};
function SkippedEnrich({ plan }: { plan: EnrichPlan }) {
  const count = plan.skipped.reduce((sum, group) => sum + group.ids.length, 0);
  return count > 0 ? (
    <>
      {" "}
      · 跳过 {count} 位（{plan.skipped.map((group) => `${ENRICH_SKIP[group.reason]} ${group.ids.length} 位`).join("、")}
      ）
    </>
  ) : null;
}
function EnrichDialog({
  ids,
  onClose,
  onDone,
}: {
  ids: number[];
  onClose: () => void;
  onDone: (result: EnrichPlan) => void;
}) {
  const client = useQueryClient();
  const preview = useQuery({ queryKey: ["previewEnrich", ids], queryFn: () => call("previewEnrich", { ids }) });
  const models = useQuery({ queryKey: ["listModelConfigs"], queryFn: () => call("listModelConfigs", {}) });
  const chosen = models.data?.find((m) => m.isDefault);
  const start = useMutation({
    mutationFn: () => call("enrichCandidates", { ids }),
    onSuccess: onDone,
    onError: () => client.invalidateQueries({ queryKey: ["listModelConfigs"] }),
  });
  const count = preview.data?.ids.length ?? 0;
  const cost =
    chosen && (chosen.inputPrice !== null || chosen.outputPrice !== null)
      ? (((chosen.inputPrice ?? 0) * 4000 + (chosen.outputPrice ?? 0) * 1000) * count) / 1_000_000
      : null;
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !start.isPending) onClose();
      }}
    >
      <DialogContent className="w-[520px]">
        <DialogHeader>补全候选信息</DialogHeader>
        <DialogBody className="space-y-4 text-sm">
          {preview.data && (
            <p>
              补全 {count} 位<SkippedEnrich plan={preview.data} />
            </p>
          )}
          <p>模型：{chosen ? `${chosen.name || chosen.model}（${chosen.model}）` : "尚未添加默认模型"}</p>
          <p className="text-muted-foreground">
            每位预计读取 1～4 页，约 5000～20000 Token；本次约 {(count * 5000).toLocaleString()}～
            {(count * 20000).toLocaleString()} Token
            {cost !== null && `，约 ¥${cost.toFixed(2)}～¥${(cost * 4).toFixed(2)}`}
            。仅为估算，以实际页面和服务商报告为准。
          </p>
          <p className="text-muted-foreground">
            只补空着的资料，保留已有值。网页内容和候选姓名、别名将发给模型服务商。可在后台暂停或取消；所属抓取已暂停时，补全也会等待继续。
          </p>
          <ErrorText error={preview.error ?? models.error ?? start.error} />
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" disabled={start.isPending} onClick={onClose}>
            关闭
          </Button>
          <ModelAction disabled={!preview.data || count === 0 || start.isPending} onWrite={() => start.mutate()}>
            {start.isPending ? "正在安排…" : "开始补全"}
          </ModelAction>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
function CandidateDetails({
  candidate: c,
  decisions,
  onChange,
}: {
  candidate: CandidateRow;
  decisions: Decisions;
  onChange: (next: Decisions | ((previous: Decisions) => Decisions)) => void;
}) {
  const detail = useQuery({ queryKey: ["getCandidate", c.id], queryFn: () => call("getCandidate", { id: c.id }) });
  const preview = useQuery({
    queryKey: ["previewCandidates", [c.id], decisions],
    queryFn: () => call("previewCandidates", { ids: [c.id], decisions }),
    enabled: c.review === "pending",
  });
  return (
    <div className="mt-4 space-y-3 border-t border-border pt-3 text-sm">
      <ErrorText error={detail.error ?? preview.error} />
      {preview.data && <IntakeChoices preview={preview.data} decisions={decisions} onChange={onChange} source="候选" />}
      {detail.data && (
        <>
          <ProfileDetails profile={detail.data.fields} />
          {detail.data.papers.length > 0 && (
            <div>
              <h3 className="font-medium">代表论文</h3>
              {detail.data.papers.map((p, n) => (
                <p key={n} className="mt-1 break-words">
                  {p}
                </p>
              ))}
            </div>
          )}
          <details>
            <summary className="cursor-pointer text-muted-foreground">
              来源与邮箱证据 · {detail.data.evidence.length} 页
            </summary>
            {detail.data.evidence.map((e, n) => (
              <div key={n} className="mt-2 break-words text-muted-foreground">
                <SourceLink url={e.url} />
                <p>
                  {e.name} · {e.emails.join("、") || "未找到可确认归属的邮箱"}
                </p>
                {e.research && <p>{e.research}</p>}
              </div>
            ))}
          </details>
          {c.review === "pending" && (
            <p className="text-muted-foreground">接受前可查看补充内容，并逐项选择资料不同的部分</p>
          )}
        </>
      )}
    </div>
  );
}
function ReviewDialog({
  ids,
  initialDecisions,
  onClose,
  onDone,
}: {
  ids: number[];
  initialDecisions: Decisions;
  onClose: () => void;
  onDone: () => void;
}) {
  const [decisions, setDecisions] = useState<Decisions>(initialDecisions);
  const preview = useQuery({
    queryKey: ["previewCandidates", ids, decisions],
    queryFn: () => call("previewCandidates", { ids, decisions }),
  });
  const accept = useMutation({
    mutationFn: () => call("acceptCandidates", { ids, decisions }),
    onSuccess: (r) => {
      if (r.skipped > 0) {
        void preview.refetch();
        setResult(`已接受 ${r.accepted} 位，另有 ${r.skipped} 位尚未接受，请查看原因`);
      } else {
        onDone();
      }
    },
  });
  const [result, setResult] = useState("");
  return (
    <Dialog
      open
      onOpenChange={(open, event) => {
        if (!open && !accept.isPending && event.reason !== "outside-press" && event.reason !== "escape-key") onClose();
      }}
    >
      <DialogContent className="w-[920px]">
        <DialogHeader>接受候选 · {ids.length} 位</DialogHeader>
        <DialogBody className="space-y-4">
          <p className="text-sm text-muted-foreground">
            只补充资料，空项不会清除本地内容。资料不同的默认保留本地，缺邮箱也可接受。
          </p>
          {preview.data && (
            <>
              <p className="rounded-md bg-muted p-3 text-sm">
                新建 {preview.data.created} 位 · 更新 {preview.data.updated} 位 · 无需修改 {preview.data.unchanged} 位
              </p>
              <IntakeChoices preview={preview.data} decisions={decisions} onChange={setDecisions} source="候选" />
              {preview.data.errors.map((e) => (
                <p key={e.row} className="text-sm text-warning">
                  第 {e.row} 位：{e.reason.replace("这些行的邮箱", "候选的邮箱").replace("或修改表格", "后再接受")}
                </p>
              ))}
              {preview.data.trashed.length > 0 && (
                <p className="text-sm text-warning">回收站中的导师默认跳过，选择“恢复并更新”后才能接受</p>
              )}
            </>
          )}
          {result && (
            <p role="status" className="text-sm text-warning">
              {result}
            </p>
          )}
          <ErrorText error={preview.error ?? accept.error} />
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" disabled={accept.isPending} onClick={onClose}>
            关闭
          </Button>
          <Button
            disabled={
              !preview.data || preview.isFetching || accept.isPending || ids.length === preview.data.errors.length
            }
            onClick={() => accept.mutate()}
          >
            {accept.isPending ? "正在接受…" : "接受所选候选"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
function ErrorText({ error }: { error: unknown }) {
  return error ? (
    <p role="alert" className="text-sm text-destructive">
      {errorMessage(error)}
    </p>
  ) : null;
}
function SourceLink({ url }: { url: string }) {
  return (
    <a
      href={url}
      className="mt-2 block break-all text-xs text-brand-text hover:underline"
      onClick={(e) => {
        e.preventDefault();
        void openUrl(url);
      }}
    >
      {url}
    </a>
  );
}
function Pager({ page, more, onPage }: { page: number; more: boolean; onPage: (page: number) => void }) {
  return (
    <div className="mt-4 flex items-center justify-center gap-3 text-sm">
      <Button variant="ghost" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        上一页
      </Button>
      <span className="text-muted-foreground">第 {page} 页</span>
      <Button variant="ghost" disabled={!more} onClick={() => onPage(page + 1)}>
        下一页
      </Button>
    </div>
  );
}
