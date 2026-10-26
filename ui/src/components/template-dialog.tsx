import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ComponentProps, useRef, useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { Template, TemplateForm } from "@/api/types";
import { Confirm } from "@/components/batch-bar";
import { formClose, Field } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { LazyLetterEditor } from "@/components/lazy-letter-editor";
import { base64, MAX_UPLOAD } from "@/file";

/** The placeholders the core fills in; they never change while the app runs. */
export function usePlaceholders() {
  return useQuery({ queryKey: ["listPlaceholders"], queryFn: () => call("listPlaceholders", {}), staleTime: Infinity });
}

const EMPTY: TemplateForm = { name: "", subject: "", bodyMd: "" };

/** Adding or editing a template (docs/ui.md#交互). Mounted while open. */
export function TemplateDialog({
  template,
  sample = EMPTY,
  placeholders,
  onClose,
}: {
  template: Template | null;
  /** What a new template starts from. */
  sample?: TemplateForm;
  placeholders: string[];
  onClose: () => void;
}) {
  const [initial] = useState(() =>
    template ? { name: template.name, subject: template.subject, bodyMd: template.bodyMd } : sample,
  );
  const [form, setForm] = useState(initial);
  // What the editor starts from; an imported body starts it anew.
  const [loaded, setLoaded] = useState({ bodyMd: initial.bodyMd, times: 0 });
  // The editor writes the body back in its own Markdown, which may differ from what was loaded
  // before anything is typed; only typing makes the body dirty.
  const [bodyEdited, setBodyEdited] = useState(false);
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: (f: TemplateForm) =>
      template ? call("updateTemplate", { ...f, id: template.id }) : call("addTemplate", f),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["listTemplates"] });
      onClose();
    },
  });
  const importWord = useMutation({
    mutationFn: async (file: File) => {
      if (file.size > MAX_UPLOAD) throw new Error("文件超过 20 MB，无法导入。");
      const [word, { pastedMarkdown }] = await Promise.all([
        call("importWordTemplate", { fileName: file.name, data: await base64(file) }),
        import("@/components/letter-editor"),
      ]);
      const bodyMd = pastedMarkdown(word.html);
      if (bodyMd.trim() === "") throw new Error("文件中没有可导入的文字，请换一个 Word 文件。");
      return { name: word.name, bodyMd };
    },
    onSuccess: ({ name, bodyMd }) => {
      setForm((f) => ({ ...f, name: f.name.trim() === "" ? name : f.name, bodyMd }));
      setLoaded((l) => ({ bodyMd, times: l.times + 1 }));
      setBodyEdited(true);
    },
  });
  const picker = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const dirty = form.name !== initial.name || form.subject !== initial.subject || bodyEdited;
  const complete = form.name.trim() !== "" && form.subject.trim() !== "" && form.bodyMd.trim() !== "";
  return (
    <Dialog open onOpenChange={formClose(dirty, save.isPending, onClose)}>
      <DialogContent className="w-[760px]" initialFocus={nameRef}>
        <form
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate(form);
          }}
        >
          <DialogHeader>{template ? "编辑模板" : "添加模板"}</DialogHeader>
          <DialogBody>
            <fieldset disabled={save.isPending} className="flex min-w-0 flex-col gap-3.5">
              <Field label="名称" id="template-name">
                <Input
                  ref={nameRef}
                  id="template-name"
                  autoComplete="off"
                  placeholder="比如：首封，偏工程方向"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </Field>
              <Field label="主题" id="template-subject">
                <Input
                  id="template-subject"
                  autoComplete="off"
                  placeholder="比如：{学校}{学院}硕士申请——{我的姓名}"
                  value={form.subject}
                  onChange={(e) => setForm({ ...form, subject: e.target.value })}
                />
              </Field>
              <Field
                label="正文"
                id="template-body"
                action={
                  <>
                    <input
                      ref={picker}
                      type="file"
                      accept=".docx,.doc"
                      hidden
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        // Cleared, so that picking the same file again imports it again.
                        e.target.value = "";
                        if (file) importWord.mutate(file);
                      }}
                    />
                    {form.bodyMd.trim() === "" ? (
                      <WordButton pending={importWord.isPending} onClick={() => picker.current?.click()} />
                    ) : (
                      <Confirm
                        side="bottom"
                        trigger={<WordButton pending={importWord.isPending} />}
                        message="正文会换成 Word 文件里的文字。"
                        action="选择文件"
                        onConfirm={() => picker.current?.click()}
                      />
                    )}
                  </>
                }
              >
                <LazyLetterEditor
                  key={loaded.times}
                  id="template-body"
                  markdown={loaded.bodyMd}
                  placeholders={placeholders}
                  disabled={save.isPending}
                  onChange={(bodyMd) => {
                    setForm((f) => ({ ...f, bodyMd }));
                    setBodyEdited(true);
                  }}
                />
                {importWord.error && (
                  <p className="mt-1.5 text-sm text-destructive">{errorMessage(importWord.error)}</p>
                )}
              </Field>
              <p className="text-sm text-muted-foreground">
                套用模板时，占位符换成这位导师的信息；“我的姓名”取自个人资料。导师缺的信息原样留下，发送前检查会提醒。
              </p>
            </fieldset>
          </DialogBody>
          <DialogFooter>
            <p className="grow text-sm text-destructive" role="alert">
              {save.isError && errorMessage(save.error)}
            </p>
            <DialogClose render={<Button variant="ghost" />}>取消</DialogClose>
            <Button type="submit" disabled={save.isPending || !complete}>
              {template ? "保存" : "添加"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Also the trigger of the confirmation, which gives it its own props. */
function WordButton({ pending, ...props }: { pending: boolean } & ComponentProps<typeof Button>) {
  return (
    <Button {...props} type="button" variant="quiet" size="sm" disabled={pending}>
      {pending ? "正在读取…" : "从 Word 导入"}
    </Button>
  );
}
