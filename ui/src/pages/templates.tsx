import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { TemplateSummary } from "@/api/types";
import { Confirm } from "@/components/batch-bar";
import { TemplateDialog, usePlaceholders } from "@/components/template-dialog";
import { Button } from "@/components/ui/button";
import { Section } from "./settings";

/** Settings › 个人 › 模板 (docs/ui.md#交互). */
export function TemplatesSection() {
  const templates = useQuery({ queryKey: ["listTemplates"], queryFn: () => call("listTemplates", {}) });
  const placeholders = usePlaceholders();
  const [adding, setAdding] = useState(false);
  const error = templates.error ?? placeholders.error;
  return (
    <Section title="模板">
      <p className="text-sm text-muted-foreground">
        套用模板写信时，占位符换成每位导师的信息。也可以让 AI 逐一撰写，不用模板。
      </p>
      {error && <p className="text-sm text-destructive">模板没能加载：{errorMessage(error)}</p>}
      {templates.data && placeholders.data && (
        <>
          {templates.data.length > 0 && (
            <ul className="flex flex-col divide-y divide-border">
              {templates.data.map((t) => (
                <TemplateRow key={t.id} template={t} placeholders={placeholders.data} />
              ))}
            </ul>
          )}
          <div>
            <Button onClick={() => setAdding(true)}>添加模板</Button>
          </div>
          {adding && (
            <TemplateDialog template={null} placeholders={placeholders.data} onClose={() => setAdding(false)} />
          )}
        </>
      )}
    </Section>
  );
}

function TemplateRow({ template: t, placeholders }: { template: TemplateSummary; placeholders: string[] }) {
  // Each edit mounts its own loader, so it starts from the template as saved now.
  const [edit, setEdit] = useState(0);
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => call("deleteTemplate", { id: t.id }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["listTemplates"] }),
  });
  return (
    <li className="flex flex-col gap-1.5 py-3">
      <div className="flex items-center gap-2 phone:flex-col phone:items-start">
        <div className="min-w-0 grow">
          <p className="truncate font-medium">{t.name}</p>
          <p className="truncate text-sm text-muted-foreground">{t.subject}</p>
        </div>
        <div className="flex gap-1">
          <Button variant="ghost" onClick={() => setEdit((n) => n + 1)}>
            编辑
          </Button>
          <Confirm
            trigger={
              <Button variant="ghost" disabled={remove.isPending}>
                删除
              </Button>
            }
            message="删除这个模板？已经用它写好的草稿不受影响。"
            action="删除"
            side="bottom"
            onConfirm={() => remove.mutate()}
          />
        </div>
      </div>
      {remove.isError && <p className="text-sm text-destructive">{errorMessage(remove.error)}</p>}
      {edit > 0 && <EditTemplate key={edit} id={t.id} placeholders={placeholders} onClose={() => setEdit(0)} />}
    </li>
  );
}

function EditTemplate({ id, placeholders, onClose }: { id: number; placeholders: string[]; onClose: () => void }) {
  // Loaded once: a failed refetch while editing would replace the open dialog with an error.
  const template = useQuery({
    queryKey: ["getTemplate", id],
    queryFn: () => call("getTemplate", { id }),
    staleTime: Infinity,
    gcTime: 0,
  });
  if (template.error) {
    return <p className="text-sm text-destructive">模板没能打开：{errorMessage(template.error)}</p>;
  }
  return template.data && <TemplateDialog template={template.data} placeholders={placeholders} onClose={onClose} />;
}
