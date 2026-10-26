import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { ChevronDownIcon } from "lucide-react";
import { useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { MentorFilter } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

/** docs/ui.md#交互: the filters under the contact states, as they are kept in the URL. */
export type FilterSearch = {
  match?: Match;
  university?: (string | null)[];
  school?: (string | null)[];
  department?: (string | null)[];
  title?: (string | null)[];
  tag?: (number | null)[];
};

type Match = 80 | 60 | 40 | "none";

const MATCHES: [Match | undefined, string][] = [
  [undefined, "不限"],
  [80, "80 分以上"],
  [60, "60 分以上"],
  [40, "40 分以上"],
  ["none", "未分析"],
];

const FIELDS = [
  ["university", "学校", "universities"],
  ["school", "学院", "schools"],
  ["department", "系所", "departments"],
  ["title", "职称", "titles"],
] as const;

/** The chosen values of a field, or undefined when none is chosen. */
function texts(v: unknown) {
  const values = Array.isArray(v)
    ? v.filter((x): x is string | null => x === null || (typeof x === "string" && x.trim() !== ""))
    : [];
  return values.length > 0 ? values : undefined;
}

/** Anything unexpected in the URL is dropped, as for the other filters. */
export function filterSearch(search: Record<string, unknown>): FilterSearch {
  const tags = Array.isArray(search.tag)
    ? search.tag.filter((x): x is number | null => x === null || Number.isSafeInteger(x))
    : [];
  return {
    match: MATCHES.find(([m]) => m !== undefined && m === search.match)?.[0],
    university: texts(search.university),
    school: texts(search.school),
    department: texts(search.department),
    title: texts(search.title),
    tag: tags.length > 0 ? tags : undefined,
  };
}

/** The part of the list query these filters make up. */
export function filterOf(s: FilterSearch): MentorFilter {
  return {
    matching:
      s.match === undefined
        ? undefined
        : s.match === "none"
          ? { kind: "unanalyzed" }
          : { kind: "atLeast", score: s.match },
    universities: s.university,
    schools: s.school,
    departments: s.department,
    titles: s.title,
    tags: s.tag,
  };
}

const NO_FILTERS = {
  match: undefined,
  university: undefined,
  school: undefined,
  department: undefined,
  title: undefined,
  tag: undefined,
} satisfies Record<keyof FilterSearch, undefined>;

export function filtered(s: FilterSearch) {
  return Object.values(filterOf(s)).some((v) => v !== undefined);
}

/** One row of menus; every change starts again from the first page. */
export function MentorFilters({ search, onChange }: { search: FilterSearch; onChange: (next: FilterSearch) => void }) {
  const options = useQuery({ queryKey: ["mentorFilterOptions"], queryFn: () => call("mentorFilterOptions", {}) });
  const o = options.data;
  return (
    <div className="flex flex-wrap items-center gap-2 px-7 pb-3 phone:flex-nowrap phone:overflow-x-auto phone:px-4 phone:pb-2.5 phone:[scrollbar-width:none]">
      <Menu
        label="匹配度"
        shown={search.match === undefined ? undefined : MATCHES.find(([m]) => m === search.match)?.[1]}
      >
        {MATCHES.map(([m, label]) => (
          <button
            key={label}
            role="menuitemradio"
            aria-checked={search.match === m}
            className={cn(ROW, "aria-checked:font-medium aria-checked:text-brand-text")}
            onClick={() => onChange({ ...search, match: m })}
          >
            {label}
          </button>
        ))}
      </Menu>
      {FIELDS.map(([key, label, list]) => (
        <Choices
          key={key}
          label={label}
          choices={o?.[list].values.map((v) => ({ value: v.value, label: v.value, count: v.count })) ?? []}
          missing={{ label: "未填写", count: o?.[list].missing ?? 0 }}
          selected={search[key] ?? []}
          error={options.error}
          onChange={(values) => onChange({ ...search, [key]: values.length > 0 ? values : undefined })}
        />
      ))}
      <Choices
        label="标签"
        choices={o?.tags.map((t) => ({ value: t.id, label: t.name, count: t.count })) ?? []}
        missing={{ label: "没有标签", count: o?.untagged ?? 0 }}
        selected={search.tag ?? []}
        error={options.error}
        onChange={(values) => onChange({ ...search, tag: values.length > 0 ? values : undefined })}
      />
      {filtered(search) && (
        <Button variant="ghost" className="ml-auto shrink-0 px-3" onClick={() => onChange(NO_FILTERS)}>
          清除筛选
        </Button>
      )}
    </div>
  );
}

const ROW = "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left hover:bg-press";

/** A pill showing what is chosen, opening a menu under it. */
function Menu({ label, shown, children }: { label: string; shown?: string; children: React.ReactNode }) {
  return (
    <Popover>
      <PopoverTrigger
        render={
          <button
            className={cn(
              "flex h-8 shrink-0 items-center gap-1 rounded-full px-3 text-sm whitespace-nowrap",
              shown
                ? "bg-selected font-medium text-brand-text"
                : "bg-muted text-muted-foreground hover:text-foreground",
            )}
          />
        }
      >
        <span className="max-w-60 truncate">{shown ? `${label}：${shown}` : label}</span>
        <ChevronDownIcon className="size-3.5" strokeWidth={1.6} />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 p-1.5">
        {children}
      </PopoverContent>
    </Popover>
  );
}

type Choice<T> = { value: T; label: string; count: number };

/** Any of the ticked values, with the mentors who have none of them at the bottom. */
function Choices<T extends string | number>({
  label,
  choices,
  missing,
  selected,
  error,
  onChange,
}: {
  label: string;
  choices: Choice<T>[];
  missing: { label: string; count: number };
  selected: (T | null)[];
  error: Error | null;
  onChange: (values: (T | null)[]) => void;
}) {
  const [text, setText] = useState("");
  const name = (v: T | null) => (v === null ? missing.label : (choices.find((c) => c.value === v)?.label ?? String(v)));
  const [first] = selected;
  const shown =
    first === undefined ? undefined : selected.length > 1 ? `${name(first)} 等 ${selected.length} 个` : name(first);
  const toggle = (v: T | null) => onChange(selected.includes(v) ? selected.filter((s) => s !== v) : [...selected, v]);
  // A chosen value nobody has any more (all moved to the bin, say) stays listed, so that it can be unticked.
  const gone = selected.flatMap((v) =>
    v === null || choices.some((c) => c.value === v) ? [] : [{ value: v, label: String(v), count: 0 }],
  );
  const listed = [...choices, ...gone].filter((c) => c.label.includes(text.trim()));
  return (
    <Menu label={label} shown={shown}>
      {choices.length > 8 && (
        <Input
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={`搜索${label}`}
          aria-label={`搜索${label}`}
          className="mb-1 h-9"
        />
      )}
      {error && <p className="px-2.5 py-2 text-destructive">没能加载：{errorMessage(error)}</p>}
      <div className="max-h-72 overflow-y-auto">
        {listed.map((c) => (
          <Option
            key={c.value}
            label={c.label}
            count={c.count}
            checked={selected.includes(c.value)}
            onToggle={() => toggle(c.value)}
          />
        ))}
        {(missing.count > 0 || selected.includes(null)) && (
          <Option
            label={missing.label}
            count={missing.count}
            checked={selected.includes(null)}
            onToggle={() => toggle(null)}
            className="text-muted-foreground"
          />
        )}
      </div>
    </Menu>
  );
}

function Option({
  label,
  count,
  checked,
  onToggle,
  className,
}: {
  label: string;
  count: number;
  checked: boolean;
  onToggle: () => void;
  className?: string;
}) {
  return (
    <label className={cn(ROW, "cursor-pointer", className)}>
      <Checkbox checked={checked} onCheckedChange={onToggle} />
      <span className="min-w-0 grow truncate">{label}</span>
      <span className="text-xs text-muted-foreground">{count}</span>
    </label>
  );
}
