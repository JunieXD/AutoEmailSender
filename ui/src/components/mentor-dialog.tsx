import { useMutation } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";
import { type ReactNode, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { errorMessage } from "@/api/call";
import type { Affiliation, MentorDetail, MentorForm } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export const EMPTY_FORM: MentorForm = {
  name: "",
  aliases: [],
  emails: [],
  affiliations: [],
  homepages: [],
  research: "",
};

const NO_AFFILIATION: Affiliation = { university: null, school: null, department: null, title: null };

const PLACES: { key: keyof Affiliation; label: string }[] = [
  { key: "university", label: "学校" },
  { key: "school", label: "学院" },
  { key: "department", label: "系所" },
  { key: "title", label: "职称" },
];

const text = (label: string, value: string, onChange: (value: string) => void) => (
  <Input aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} />
);

/** The form for a mentor's existing fields; blank ones stay blank. */
export function formOf(m: MentorDetail): MentorForm {
  return {
    name: m.name,
    aliases: m.aliases,
    emails: m.emails,
    affiliations: m.affiliations,
    homepages: m.homepages,
    research: m.research ?? "",
  };
}

/**
 * Adding or editing a mentor's own fields. The core trims and checks them, leaves out blank rows and
 * says what to fix. Mounted while open, so every opening starts from `initial`.
 */
export function MentorDialog<T>({
  title,
  action,
  initial,
  save,
  onSaved,
  onClose,
}: {
  title: string;
  action: string;
  initial: MentorForm;
  save: (form: MentorForm) => Promise<T>;
  onSaved: (result: T) => void | Promise<void>;
  onClose: () => void;
}) {
  const [form, setForm] = useState(initial);
  const mutation = useMutation({ mutationFn: save, onSuccess: onSaved });
  const dirty = JSON.stringify(form) !== JSON.stringify(initial);
  const nameRef = useRef<HTMLInputElement>(null);
  return (
    <Dialog
      open
      onOpenChange={(open, details) => {
        // docs/development.md#界面: what the user typed is not thrown away by a stray click or Esc,
        // nor while it is being saved.
        const stray = details.reason === "outside-press" || details.reason === "escape-key";
        if (!open && !mutation.isPending && !(dirty && stray)) onClose();
      }}
    >
      <DialogContent initialFocus={nameRef}>
        <form
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate(form);
          }}
        >
          <DialogHeader>{title}</DialogHeader>
          <DialogBody>
            <fieldset disabled={mutation.isPending} className="flex min-w-0 flex-col gap-3.5">
              <div>
                <label htmlFor="mentor-name" className="mb-1.5 block text-sm text-muted-foreground">
                  姓名
                </label>
                <Input
                  ref={nameRef}
                  id="mentor-name"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </div>
              <Rows
                label="别名"
                add="添加别名"
                items={form.aliases}
                blank=""
                onChange={(aliases) => setForm({ ...form, aliases })}
              >
                {(alias, update) => text("别名", alias, update)}
              </Rows>
              <Rows
                label="邮箱"
                add="添加邮箱"
                first={["发信", "设为发信地址"]}
                items={form.emails}
                blank=""
                onChange={(emails) => setForm({ ...form, emails })}
              >
                {(email, update) => text("邮箱", email, update)}
              </Rows>
              <Rows
                label="单位"
                add="添加单位"
                first={["主要", "设为主要"]}
                items={form.affiliations}
                blank={NO_AFFILIATION}
                onChange={(affiliations) => setForm({ ...form, affiliations })}
              >
                {(a, update) => (
                  <div className="grid grid-cols-2 gap-2 py-1.5 phone:grid-cols-1">
                    {PLACES.map((p) => (
                      <Input
                        key={p.key}
                        aria-label={p.label}
                        placeholder={p.label}
                        value={a[p.key] ?? ""}
                        onChange={(e) => update({ ...a, [p.key]: e.target.value })}
                      />
                    ))}
                  </div>
                )}
              </Rows>
              <Rows
                label="主页"
                add="添加主页"
                items={form.homepages}
                blank=""
                onChange={(homepages) => setForm({ ...form, homepages })}
              >
                {(url, update) => text("主页", url, update)}
              </Rows>
              <div>
                <label htmlFor="mentor-research" className="mb-1.5 block text-sm text-muted-foreground">
                  研究方向
                </label>
                <Textarea
                  id="mentor-research"
                  rows={3}
                  value={form.research}
                  onChange={(e) => setForm({ ...form, research: e.target.value })}
                />
              </div>
            </fieldset>
          </DialogBody>
          <DialogFooter>
            <p className="grow text-sm text-destructive" role="alert">
              {mutation.isError && errorMessage(mutation.error)}
            </p>
            <DialogClose render={<Button variant="ghost" />}>取消</DialogClose>
            <Button type="submit" disabled={mutation.isPending || form.name.trim() === ""}>
              {action}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Something a mentor may have several of, a row each. With more than one, the first is marked
 * (docs/data-model.md#导师) and any other can be moved first, and each can be taken out.
 */
function Rows<T>({
  label,
  add,
  first,
  items,
  blank,
  onChange,
  children,
}: {
  label: string;
  add: string;
  /** What marks the first row, and what moves another row first. */
  first?: [mark: string, promote: string];
  items: T[];
  blank: T;
  onChange: (items: T[]) => void;
  children: (item: T, update: (item: T) => void) => ReactNode;
}) {
  // An empty list shows one blank row to type in.
  const rows = items.length > 0 ? items : [blank];
  const several = rows.length > 1;
  const list = useRef<HTMLDivElement>(null);
  // Keyboard and screen reader users stay with the row they moved, the one after the row they took
  // out, or the row they added.
  const change = (next: T[], focus: number) => {
    flushSync(() => onChange(next));
    list.current?.children[focus]?.querySelector("input")?.focus();
  };
  return (
    <div role="group" aria-label={label}>
      <div className="mb-1.5 text-sm text-muted-foreground">{label}</div>
      <div ref={list} className="flex flex-col gap-2">
        {rows.map((item, i) => (
          // Rows have nothing steadier than their place to tell them apart.
          <div key={i} className="flex items-center gap-2">
            <div className="min-w-0 grow">{children(item, (next) => onChange(rows.with(i, next)))}</div>
            {several && first && i === 0 && (
              <span className="w-[86px] shrink-0 text-center text-sm text-muted-foreground">{first[0]}</span>
            )}
            {several && first && i > 0 && (
              <Button
                type="button"
                variant="quiet"
                size="sm"
                className="w-[86px]"
                onClick={() => change([item, ...rows.toSpliced(i, 1)], 0)}
              >
                {first[1]}
              </Button>
            )}
            {several && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label="去掉"
                onClick={() => change(rows.toSpliced(i, 1), Math.min(i, rows.length - 2))}
              >
                <X strokeWidth={1.6} />
              </Button>
            )}
          </div>
        ))}
      </div>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="mt-1.5 -ml-2.5"
        onClick={() => change([...rows, blank], rows.length)}
      >
        <Plus strokeWidth={1.6} />
        {add}
      </Button>
    </div>
  );
}
