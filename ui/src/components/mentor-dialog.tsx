import { useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { errorMessage } from "@/api/call";
import type { MentorDetail, MentorForm } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

const FIELDS: { key: keyof MentorForm; label: string; wide?: boolean }[] = [
  { key: "name", label: "姓名" },
  { key: "title", label: "职称" },
  { key: "email", label: "邮箱" },
  { key: "homepageUrl", label: "主页" },
  { key: "university", label: "学校" },
  { key: "school", label: "学院" },
  { key: "department", label: "系所" },
  { key: "research", label: "研究方向", wide: true },
];

export const EMPTY_FORM: MentorForm = {
  name: "",
  title: "",
  email: "",
  homepageUrl: "",
  university: "",
  school: "",
  department: "",
  research: "",
};

/** The form for a mentor's existing fields; blank ones stay blank. */
export function formOf(m: MentorDetail): MentorForm {
  return {
    name: m.name,
    title: m.title ?? "",
    email: m.email ?? "",
    homepageUrl: m.homepageUrl ?? "",
    university: m.university ?? "",
    school: m.school ?? "",
    department: m.department ?? "",
    research: m.research ?? "",
  };
}

/**
 * Adding or editing a mentor's own fields. The core trims and checks them and says what to fix.
 * Mounted while open, so every opening starts from `initial`.
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
  const dirty = FIELDS.some((f) => form[f.key] !== initial[f.key]);
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
            <fieldset
              disabled={mutation.isPending}
              className="grid min-w-0 grid-cols-2 gap-x-4 gap-y-3.5 phone:grid-cols-1"
            >
              {FIELDS.map((f) => {
                const props = {
                  id: `mentor-${f.key}`,
                  value: form[f.key],
                  onChange: (e: { target: { value: string } }) => setForm({ ...form, [f.key]: e.target.value }),
                };
                return (
                  <div key={f.key} className={f.wide ? "col-span-full" : ""}>
                    <label htmlFor={props.id} className="mb-1.5 block text-sm text-muted-foreground">
                      {f.label}
                    </label>
                    {f.wide ? (
                      <Textarea rows={3} {...props} />
                    ) : (
                      <Input ref={f.key === "name" ? nameRef : undefined} {...props} />
                    )}
                  </div>
                );
              })}
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
