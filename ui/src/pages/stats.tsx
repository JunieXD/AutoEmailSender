import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { call, errorMessage } from "@/api/call";
import type { Range } from "@/api/types";

const RANGES: Record<Range, string> = { week: "最近 7 天", month: "最近 30 天", quarter: "最近 90 天", all: "全部" };

export function statsSearch(search: Record<string, unknown>): { page: number } {
  const page = Number(search.page);
  return { page: Number.isSafeInteger(page) && page > 0 && page <= 4_294_967_295 ? page : 1 };
}

function savedRange(): Range {
  const saved = localStorage.getItem("statsRange");
  return saved === "week" || saved === "quarter" || saved === "all" ? saved : "month";
}

/** Temporary API viewer; the final statistics layout is implemented separately. */
export function StatsPage() {
  const [range, setRange] = useState(savedRange);
  const [now, setNow] = useState(Date.now);
  const { page } = useSearch({ from: "/stats" });
  const navigate = useNavigate({ from: "/stats" });
  const timezone = new Intl.DateTimeFormat().resolvedOptions().timeZone;
  const input = { range, now, timezone, page };
  const contact = useQuery({ queryKey: ["contactStats", input], queryFn: () => call("contactStats", input) });
  const mentors = useQuery({ queryKey: ["mentorStats"], queryFn: () => call("mentorStats", {}) });
  const tokens = useQuery({
    queryKey: ["tokenStats", range, now, timezone],
    queryFn: () => call("tokenStats", { range, now, timezone }),
  });
  const pages = Math.max(1, Math.ceil((contact.data?.schoolCount ?? 0) / 10));
  return (
    <div className="flex-1 space-y-6 overflow-auto p-6">
      <h1>统计</h1>
      <label>
        时间范围{" "}
        <select
          aria-label="时间范围"
          value={range}
          onChange={(event) => {
            const value = event.target.value;
            if (value === "week" || value === "month" || value === "quarter" || value === "all") {
              setRange(value);
              localStorage.setItem("statsRange", value);
              setNow(Date.now());
              void navigate({ search: { page: 1 } });
            }
          }}
        >
          {Object.entries(RANGES).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <button
        onClick={() => {
          setNow(Date.now());
          void mentors.refetch();
        }}
      >
        刷新
      </button>
      <p>本地时区：{timezone}。下方为核心接口返回的原始数据。</p>
      <section aria-label="联系进度">
        <h2>联系进度</h2>
        {contact.isPending ? (
          <p>正在读取…</p>
        ) : contact.error ? (
          <p role="alert">{errorMessage(contact.error)}</p>
        ) : (
          <pre>{JSON.stringify(contact.data, null, 2)}</pre>
        )}
        <div>
          院校第 {page} / {pages} 页{" "}
          <button disabled={page <= 1} onClick={() => void navigate({ search: { page: page - 1 } })}>
            上一页
          </button>{" "}
          <button disabled={page >= pages} onClick={() => void navigate({ search: { page: page + 1 } })}>
            下一页
          </button>
        </div>
      </section>
      <section aria-label="导师概览">
        <h2>导师概览</h2>
        {mentors.isPending ? (
          <p>正在读取…</p>
        ) : mentors.error ? (
          <p role="alert">{errorMessage(mentors.error)}</p>
        ) : (
          <pre>{JSON.stringify(mentors.data, null, 2)}</pre>
        )}
      </section>
      <section aria-label="Token 用量">
        <h2>Token 用量</h2>
        {tokens.isPending ? (
          <p>正在读取…</p>
        ) : tokens.error ? (
          <p role="alert">{errorMessage(tokens.error)}</p>
        ) : (
          <pre>{JSON.stringify(tokens.data, null, 2)}</pre>
        )}
      </section>
    </div>
  );
}
