import { useMemo, useState } from "react";
import type { Difference, Field } from "@/api/types";
import { Changes } from "@/components/changes";
import { placeOf } from "@/components/mentor";
import { Button } from "@/components/ui/button";
import { changes } from "@/diff";

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

/** Decisions stay as the complete compared values, so a later change cannot reuse a stale choice. */
export function Differences({
  items,
  take,
  onChange,
}: {
  items: Difference[];
  take: Difference[];
  onChange: (take: Difference[]) => void;
}) {
  const groups = useMemo(() => {
    const byField = new Map<Field, Difference[]>();
    for (const d of items) {
      const group = byField.get(d.field) ?? [];
      group.push(d);
      byField.set(d.field, group);
    }
    return [...byField];
  }, [items]);
  const [individual, setIndividual] = useState<ReadonlySet<Field>>(new Set());
  return (
    <div className="flex flex-col gap-3">
      {groups.map(([field, group]) => (
        <DifferenceGroup
          key={field}
          field={field}
          items={group}
          take={take}
          individual={individual.has(field)}
          onAll={(incoming) => {
            setIndividual((s) => new Set([...s].filter((f) => f !== field)));
            onChange([...take.filter((d) => d.field !== field), ...(incoming ? group : [])]);
          }}
          onOne={(d, incoming) => {
            setIndividual((s) => new Set(s).add(field));
            onChange([...take.filter((v) => v !== d), ...(incoming ? [d] : [])]);
          }}
        />
      ))}
    </div>
  );
}

function DifferenceGroup({
  field,
  items,
  take,
  individual,
  onAll,
  onOne,
}: {
  field: Field;
  items: Difference[];
  take: Difference[];
  individual: boolean;
  onAll: (incoming: boolean) => void;
  onOne: (item: Difference, incoming: boolean) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [limit, setLimit] = useState(30);
  const all = items.every((d) => take.includes(d));
  return (
    <section className="rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <strong className="mr-auto phone:basis-full">
          {FIELD_NAMES[field]} · {new Set(items.map((d) => d.id)).size} 位
        </strong>
        {individual && <span className="text-xs text-muted-foreground">已逐个选</span>}
        <div role="group" aria-label={`${FIELD_NAMES[field]}的选择`} className="flex gap-1">
          <Button
            size="sm"
            variant={!all && !individual ? "quiet" : "ghost"}
            aria-pressed={!all && !individual}
            onClick={() => onAll(false)}
          >
            保留本地的
          </Button>
          <Button
            size="sm"
            variant={all && !individual ? "quiet" : "ghost"}
            aria-pressed={all && !individual}
            onClick={() => onAll(true)}
          >
            用表格的
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
                  第 {d.row} 行{d.affiliation && ` · ${placeOf(d.affiliation)}`}
                </span>
              </p>
              <div
                className="grid grid-cols-2 gap-2 phone:grid-cols-1"
                role="group"
                aria-label={`${d.name}的${FIELD_NAMES[field]}`}
              >
                {[false, true].map((incoming) => (
                  <button
                    key={String(incoming)}
                    type="button"
                    aria-pressed={take.includes(d) === incoming}
                    onClick={() => onOne(d, incoming)}
                    className="min-w-0 rounded-md border border-border p-3 text-left break-words whitespace-pre-wrap aria-pressed:border-brand aria-pressed:bg-selected"
                  >
                    <span className="mb-1 block text-xs text-muted-foreground">{incoming ? "表格的" : "本地的"}</span>
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
