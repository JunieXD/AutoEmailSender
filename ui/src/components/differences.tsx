import { useMemo, useState } from "react";
import type { Affiliation, Field } from "@/api/types";
import { Changes } from "@/components/changes";
import { placeOf } from "@/components/mentor";
import { Button } from "@/components/ui/button";
import { changes } from "@/diff";
import { sameDifference } from "@/import-decisions";

export const FIELD_NAMES: Record<Field, string> = {
  name: "姓名",
  email: "邮箱",
  university: "学校",
  school: "学院",
  department: "系所",
  title: "职称",
  homepage: "主页",
  alias: "别名",
  research: "研究方向",
  papers: "代表论文",
  sourceUrl: "来源网址",
  tags: "标签",
  note: "备注",
};

/** One value that differs; spreadsheet rows, candidates and mentor library updates all compare this way. */
export type Compared<F extends string> = {
  row?: number;
  id: number;
  name: string;
  field: F;
  affiliation: Affiliation | null;
  local: string;
  incoming: string;
};

/** Decisions stay as the complete compared values, so a later change cannot reuse a stale choice. */
export function Differences<F extends string, T extends Compared<F>>({
  items,
  take,
  onChange,
  names,
  source,
  numbered,
}: {
  items: T[];
  take: T[];
  onChange: (take: T[]) => void;
  names: Record<F, string>;
  source: string;
  /** Whether the rows mean something to the user, like the lines of a spreadsheet. */
  numbered: boolean;
}) {
  const groups = useMemo(() => {
    const byField = new Map<F, T[]>();
    for (const d of items) {
      const group = byField.get(d.field) ?? [];
      group.push(d);
      byField.set(d.field, group);
    }
    return [...byField];
  }, [items]);
  const [individual, setIndividual] = useState<ReadonlySet<F>>(new Set());
  return (
    <div className="flex flex-col gap-3">
      {groups.map(([field, group]) => (
        <DifferenceGroup
          source={source}
          numbered={numbered}
          key={field}
          label={names[field]}
          items={group}
          take={take}
          individual={individual.has(field)}
          onAll={(incoming) => {
            setIndividual((s) => new Set([...s].filter((f) => f !== field)));
            onChange([...take.filter((d) => d.field !== field), ...(incoming ? group : [])]);
          }}
          onOne={(d, incoming) => {
            setIndividual((s) => new Set(s).add(field));
            onChange([...take.filter((v) => !sameDifference(v, d)), ...(incoming ? [d] : [])]);
          }}
        />
      ))}
    </div>
  );
}

function DifferenceGroup<T extends Compared<string>>({
  source,
  numbered,
  label,
  items,
  take,
  individual,
  onAll,
  onOne,
}: {
  source: string;
  numbered: boolean;
  label: string;
  items: T[];
  take: T[];
  individual: boolean;
  onAll: (incoming: boolean) => void;
  onOne: (item: T, incoming: boolean) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [limit, setLimit] = useState(30);
  const taken = items.filter((d) => take.some((choice) => sameDifference(choice, d))).length;
  const all = taken === items.length;
  // Defaults may already differ between mentors, as with mentor library updates.
  const mixed = individual || (taken > 0 && !all);
  return (
    <section className="rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <strong className="mr-auto phone:basis-full">
          {label} · {new Set(items.map((d) => d.id)).size} 位
        </strong>
        {mixed && <span className="text-xs text-muted-foreground">已逐个选</span>}
        <div role="group" aria-label={`${label}的选择`} className="flex gap-1">
          <Button
            size="sm"
            variant={!all && !mixed ? "quiet" : "ghost"}
            aria-pressed={!all && !mixed}
            onClick={() => onAll(false)}
          >
            保留本地的
          </Button>
          <Button
            size="sm"
            variant={all && !mixed ? "quiet" : "ghost"}
            aria-pressed={all && !mixed}
            onClick={() => onAll(true)}
          >
            用{source}的
          </Button>
        </div>
        <Button size="sm" variant="ghost" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
          {expanded ? "收起" : "逐个看"}
        </Button>
      </div>
      {expanded && (
        <div className="mt-3 flex flex-col gap-3">
          {items.slice(0, limit).map((d, i) => (
            <div key={i} className="min-w-0 text-sm">
              <p className="mb-1.5 font-medium">
                {d.name}
                <span className="ml-2 text-xs font-normal text-muted-foreground">
                  {[numbered && d.row !== undefined && `第 ${d.row} 项`, d.affiliation && placeOf(d.affiliation)]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </p>
              <div className="grid grid-cols-2 gap-2 phone:grid-cols-1" role="group" aria-label={`${d.name}的${label}`}>
                {[false, true].map((incoming) => (
                  <button
                    key={String(incoming)}
                    type="button"
                    aria-pressed={take.some((choice) => sameDifference(choice, d)) === incoming}
                    onClick={() => onOne(d, incoming)}
                    className="min-w-0 rounded-md border border-border p-3 text-left break-words whitespace-pre-wrap aria-pressed:border-brand aria-pressed:bg-selected"
                  >
                    <span className="mb-1 block text-xs text-muted-foreground">
                      {incoming ? `${source}的` : "本地的"}
                    </span>
                    <span>{incoming ? d.incoming : d.local}</span>
                  </button>
                ))}
              </div>
              {(d.local.length > 40 || d.incoming.length > 40) && (
                <div className="mt-2 rounded-md bg-muted p-3">
                  <Changes parts={changes(d.local, d.incoming)} />
                </div>
              )}
            </div>
          ))}
          {items.length > limit && (
            <Button variant="ghost" onClick={() => setLimit((n) => n + 30)}>
              再显示 {Math.min(30, items.length - limit)} 项（尚余 {items.length - limit} 项）
            </Button>
          )}
        </div>
      )}
    </section>
  );
}
