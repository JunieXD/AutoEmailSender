import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { cn } from "cn";
import { call, errorMessage } from "@/api/call";
import type { ContactProgress, MentorOverview, Range, Trend, TokenUsage, UsagePurpose } from "@/api/types";
import { BarChart, Legend, type Point, type Series } from "@/components/bar-chart";
import { Pager } from "@/components/mentor-table";
import { findSearch } from "@/pages/find";
import { Segments } from "@/components/ui/segments";

// docs/ui.md 统计. Only the contact progress and token sections follow the range.

const RANGES: [Range, string][] = [
  ["week", "7 天"],
  ["month", "30 天"],
  ["quarter", "90 天"],
  ["all", "全部"],
];
const TREND: readonly [Series, Series] = [
  { name: "发出", unit: "封", color: "var(--primary)" },
  { name: "回信", unit: "封", color: "var(--info)" },
];
const PURPOSES: Record<UsagePurpose, string> = {
  crawl: "智能抓取",
  match: "匹配分析",
  draft: "AI 起草",
  rewrite: "AI 改写",
  fact_check: "AI 核对",
  reply_outcome: "回信标记",
  reply_todos: "回信待办",
};
const SCHOOLS_PER_PAGE = 10;

function savedRange(): Range {
  const saved = localStorage.getItem("statsRange");
  return RANGES.find(([r]) => r === saved)?.[0] ?? "month";
}

export function StatsPage() {
  const [range, setRange] = useState(savedRange);
  const [now, setNow] = useState(Date.now);
  const [page, setPage] = useState(1);
  const timezone = new Intl.DateTimeFormat().resolvedOptions().timeZone;
  const period = { range, now, timezone };
  // The ranges end today: a page left open past midnight moves on with the date.
  useEffect(() => {
    const timer = setInterval(() => {
      if (new Date().toDateString() !== new Date(now).toDateString()) {
        setNow(Date.now());
        setPage(1);
      }
    }, 60_000);
    return () => clearInterval(timer);
  }, [now]);
  const contact = useQuery({
    queryKey: ["stats", "contact", range, now, timezone, page],
    queryFn: () => call("contactStats", { ...period, page }),
    // Turning a page of schools keeps the rest in place; another range is drawn afresh.
    placeholderData: (previous, last) =>
      last?.queryKey[2] === range && last.queryKey[3] === now ? previous : undefined,
  });
  const mentors = useQuery({ queryKey: ["stats", "mentors"], queryFn: () => call("mentorStats", {}) });
  const usage = useQuery({ queryKey: ["stats", "tokens", period], queryFn: () => call("tokenStats", period) });
  return (
    <div className="@container flex-1 overflow-y-auto">
      <header className="flex max-w-[1120px] flex-wrap items-center gap-x-4 gap-y-3 px-7 pt-6 pb-2 phone:px-4 phone:pt-3.5">
        <h1 className="grow text-2xl font-semibold phone:text-[22px]">统计</h1>
        <Segments
          label="时间范围"
          value={range}
          options={RANGES}
          onChange={(r) => {
            localStorage.setItem("statsRange", r);
            setRange(r);
            setNow(Date.now());
            setPage(1);
          }}
        />
      </header>
      <div className="max-w-[1120px] space-y-12 px-7 pt-4 pb-12 phone:space-y-10 phone:px-4">
        <Section
          title="联系进度"
          note={range === "all" ? "全部时间" : `最近 ${RANGES.find(([r]) => r === range)?.[1]}`}
        >
          <Loaded query={contact}>{(c) => <Contact progress={c} range={range} page={page} onPage={setPage} />}</Loaded>
        </Section>
        <Section title="导师" note="现有的全部导师，不含回收站">
          <Loaded query={mentors}>{(m) => <Mentors overview={m} />}</Loaded>
        </Section>
        <Section title="Token 用量" note="按模型配置里填的单价估算费用">
          <Loaded query={usage}>{(t) => <Tokens usage={t} />}</Loaded>
        </Section>
      </div>
    </div>
  );
}

function Section({ title, note, children }: { title: string; note: string; children: React.ReactNode }) {
  return (
    <section aria-label={title}>
      <div className="mb-5 flex items-baseline gap-3">
        <h2 className="text-lg font-semibold">{title}</h2>
        <p className="text-sm text-muted-foreground">{note}</p>
      </div>
      {children}
    </section>
  );
}

function Loaded<T>({
  query,
  children,
}: {
  query: { data: T | undefined; error: Error | null };
  children: (data: T) => React.ReactNode;
}) {
  if (query.error) return <p className="text-sm text-destructive">{errorMessage(query.error)}</p>;
  // The queries take milliseconds; drawing nothing until then avoids a flash of placeholders.
  return query.data === undefined ? <div className="h-40" /> : children(query.data);
}

function Contact({
  progress: p,
  range,
  page,
  onPage,
}: {
  progress: ContactProgress;
  range: Range;
  page: number;
  onPage: (page: number) => void;
}) {
  if (p.contacted === 0 && p.trend.every((t) => t.sent === 0 && t.replies === 0))
    return (
      <Empty>
        这段时间还没有发出的信。去
        <Link to="/tasks" className="mx-1 text-brand-text hover:underline">
          任务
        </Link>
        里写第一封吧。
      </Empty>
    );
  const days = p.medianDays === null ? null : Math.max(1, Math.ceil(p.medianDays));
  return (
    <div className="space-y-10">
      <div className="grid grid-cols-4 rounded-xl bg-muted py-4 @max-[640px]:grid-cols-2 @max-[640px]:gap-y-4">
        <Figure label="已联系" value={p.contacted} unit="位" detail="第一封信在这段时间发出" />
        <Figure
          label="已回复"
          value={p.replied}
          unit="位"
          detail={`回复率 ${Math.round((p.replied / Math.max(1, p.contacted)) * 100)}%`}
        />
        <Figure
          label="首次回复"
          value={days ?? "—"}
          unit={days === null ? "" : "天内"}
          detail={days === null ? "还没有回信" : "一半的导师在这个时间内回复"}
        />
        <Figure label="退信" value={p.bounced} unit="封" detail="这段时间发出后被退回" />
      </div>
      <TrendView trend={p.trend} monthly={range === "all"} />
      <div className="grid grid-cols-[2fr_3fr] gap-10 @max-[780px]:grid-cols-1">
        <div>
          <h3 className="mb-1 font-medium">首次回复用了多久</h3>
          <p className="mb-4 text-sm text-muted-foreground">这段时间联系、已经回复的 {p.replied} 位导师</p>
          <Bars
            rows={[
              { label: "1 天内", value: p.waiting[0] },
              { label: "1～3 天", value: p.waiting[1] },
              { label: "3～7 天", value: p.waiting[2] },
              { label: "1～2 周", value: p.waiting[3] },
              { label: "2 周以上", value: p.waiting[4] },
            ]}
            unit="位"
          />
        </div>
        <div>
          <h3 className="mb-1 font-medium">院校对比</h3>
          <p className="mb-3 text-sm text-muted-foreground">按联系的导师数排列，导师在几所学校任职的各算一次</p>
          <table className="w-full text-sm">
            <thead className="text-left text-muted-foreground">
              <tr className="border-b border-border">
                <th className="py-2 font-normal">学校</th>
                <th className="w-20 py-2 text-right font-normal">已联系</th>
                <th className="w-20 py-2 text-right font-normal">已回复</th>
                <th className="w-20 py-2 text-right font-normal">回复率</th>
              </tr>
            </thead>
            <tbody>
              {p.schools.map((s) => (
                <tr key={s.university} className="border-b border-border">
                  <td className="py-2.5 pr-3">{s.university}</td>
                  <td className="py-2.5 text-right tabular-nums">{s.contacted}</td>
                  <td className="py-2.5 text-right tabular-nums">{s.replied}</td>
                  <td className="py-2.5 text-right tabular-nums">
                    {Math.round((s.replied / Math.max(1, s.contacted)) * 100)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {p.schoolCount === 0 && <p className="py-6 text-sm text-muted-foreground">联系过的导师都还没有填学校。</p>}
          {/* Also past the end, when the list shrank under the page shown. */}
          {(p.schoolCount > SCHOOLS_PER_PAGE || page > 1) && (
            <div className="mt-3 flex justify-end text-sm text-muted-foreground">
              <Pager page={page} total={p.schoolCount} pageSize={SCHOOLS_PER_PAGE} onPage={onPage} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Figure({
  label,
  value,
  unit,
  detail,
}: {
  label: string;
  value: number | string;
  unit: string;
  detail: string;
}) {
  return (
    <div className="border-l border-border px-5 first:border-l-0 @max-[640px]:nth-3:border-l-0">
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className="mt-1 text-[28px] leading-9 font-semibold tabular-nums">
        {value}
        {unit && <span className="ml-1 text-sm font-normal text-muted-foreground">{unit}</span>}
      </p>
      <p className="mt-0.5 text-xs text-muted-foreground">{detail}</p>
    </div>
  );
}

function TrendView({ trend, monthly }: { trend: Trend[]; monthly: boolean }) {
  const [table, setTable] = useState(false);
  const points: Point[] = trend.map((t) => ({ ...dateLabels(t.date, monthly), values: [t.sent, t.replies] }));
  const title = `${monthly ? "每月" : "每天"}发出的信和收到的回信`;
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2">
        <h3 className="grow font-medium">{title}</h3>
        <Legend series={TREND} />
        <button className="text-sm text-brand-text hover:underline" onClick={() => setTable(!table)}>
          {table ? "看图表" : "看表格"}
        </button>
      </div>
      {table ? (
        <div className="max-h-72 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-background text-left text-muted-foreground">
              <tr className="border-b border-border">
                <th className="py-2 font-normal">{monthly ? "月份" : "日期"}</th>
                <th className="w-24 py-2 text-right font-normal">发出（封）</th>
                <th className="w-24 py-2 text-right font-normal">回信（封）</th>
              </tr>
            </thead>
            <tbody>
              {points.toReversed().map((p) => (
                <tr key={p.title} className="border-b border-border">
                  <td className="py-2">{p.title}</td>
                  <td className="py-2 text-right tabular-nums">{p.values[0]}</td>
                  <td className="py-2 text-right tabular-nums">{p.values[1]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <BarChart series={TREND} points={points} caption={title} />
      )}
    </div>
  );
}

/** "10月25日" or "2026年10月", with the year on axis labels only where it changes. */
function dateLabels(date: string, monthly: boolean): { label: string; title: string } {
  const [y, m, d] = date.split("-").map(Number);
  if (monthly) return { label: m === 1 ? `${y}年1月` : `${m}月`, title: `${y}年${m}月` };
  return { label: `${m}月${d}日`, title: `${y}年${m}月${d}日` };
}

function Mentors({ overview: o }: { overview: MentorOverview }) {
  if (o.total === 0)
    return (
      <Empty>
        还没有导师。去
        <Link to="/find" search={findSearch({})} className="mx-1 text-brand-text hover:underline">
          找导师
        </Link>
        抓取或导入吧。
      </Empty>
    );
  return (
    <div className="grid grid-cols-3 gap-10 @max-[900px]:grid-cols-1">
      <div>
        <h3 className="mb-4 font-medium">匹配度</h3>
        <Bars
          rows={[
            { label: "80～100 分", value: o.matching[4] },
            { label: "60～79 分", value: o.matching[3] },
            { label: "40～59 分", value: o.matching[2] },
            { label: "20～39 分", value: o.matching[1] },
            { label: "0～19 分", value: o.matching[0] },
          ]}
          unit="位"
        />
        {o.matching[5] > 0 && <p className="mt-3 text-sm text-muted-foreground">另有 {o.matching[5]} 位还没分析</p>}
      </div>
      <div>
        <h3 className="mb-4 font-medium">学校</h3>
        <Bars rows={o.schools.map((s) => ({ label: s.university, value: s.count }))} unit="位" />
        {o.otherSchools > 0 && <p className="mt-3 text-sm text-muted-foreground">其他学校共 {o.otherSchools} 人次</p>}
      </div>
      <div>
        <h3 className="mb-4 font-medium">资料完整度</h3>
        <Bars
          rows={[
            { label: "有邮箱", value: o.email },
            { label: "有研究方向", value: o.research },
            { label: "有代表论文", value: o.papers },
            { label: "有主页", value: o.homepage },
          ]}
          max={o.total}
          unit="位"
          share
        />
        <p className="mt-3 text-sm text-muted-foreground">共 {o.total} 位导师</p>
      </div>
    </div>
  );
}

/** Labelled horizontal bars; the count is written beside each bar, so no axis or table is needed. */
function Bars({
  rows,
  unit,
  max = Math.max(1, ...rows.map((r) => r.value)),
  share = false,
}: {
  rows: { label: string; value: number }[];
  unit: string;
  max?: number;
  share?: boolean;
}) {
  return (
    <ul className="space-y-3 text-sm">
      {rows.map((r) => (
        <li key={r.label} className="grid grid-cols-[minmax(4.5rem,8rem)_1fr_auto] items-center gap-3">
          <span className="truncate text-muted-foreground" title={r.label}>
            {r.label}
          </span>
          <span className="h-2 rounded-full bg-muted">
            <span className="block h-full rounded-full bg-primary/75" style={{ width: `${(r.value / max) * 100}%` }} />
          </span>
          <span className="w-16 text-right tabular-nums">
            {share ? `${Math.round((r.value / max) * 100)}%` : `${r.value} ${unit}`}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Tokens({ usage }: { usage: TokenUsage }) {
  if (usage.purposes.length === 0) return <Empty>这段时间没有用过模型。</Empty>;
  const unpriced = usage.total.unpricedCalls;
  return (
    <>
      <div className="max-w-3xl">
        <table className="w-full text-sm">
          <thead className="text-left text-muted-foreground">
            <tr className="border-b border-border">
              <th className="py-2 font-normal">用途</th>
              <th className="py-2 text-right font-normal">输入</th>
              <th className="py-2 text-right font-normal phone:hidden">其中缓存命中</th>
              <th className="py-2 text-right font-normal">输出</th>
              <th className="py-2 text-right font-normal">估算费用</th>
            </tr>
          </thead>
          <tbody>
            {usage.purposes.map((u) => (
              <TokenRow key={u.purpose} label={PURPOSES[u.purpose]} row={u} />
            ))}
            <TokenRow label="合计" row={usage.total} total />
          </tbody>
        </table>
      </div>
      {unpriced > 0 && (
        <p className="mt-3 text-sm text-muted-foreground">
          有 {unpriced} 次调用的模型配置没填单价或已删除，费用里没有算它们。
        </p>
      )}
    </>
  );
}

function TokenRow({
  label,
  row,
  total = false,
}: {
  label: string;
  row: { input: number; cached: number; output: number; estimatedCost: number | null };
  total?: boolean;
}) {
  return (
    <tr className={cn("border-b border-border", total && "border-b-0 font-medium")}>
      <td className="py-2.5">{label}</td>
      <td className="py-2.5 text-right tabular-nums">{tokens(row.input)}</td>
      <td className="py-2.5 text-right text-muted-foreground tabular-nums phone:hidden">{tokens(row.cached)}</td>
      <td className="py-2.5 text-right tabular-nums">{tokens(row.output)}</td>
      <td className="py-2.5 text-right tabular-nums">{cost(row.estimatedCost)}</td>
    </tr>
  );
}

function tokens(n: number): string {
  return n.toLocaleString();
}

function cost(yuan: number | null): string {
  if (yuan === null) return "—";
  return yuan > 0 && yuan < 0.01 ? "不到 ¥0.01" : `¥${yuan.toFixed(2)}`;
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="rounded-xl bg-muted px-5 py-8 text-center text-sm text-muted-foreground">{children}</p>;
}
