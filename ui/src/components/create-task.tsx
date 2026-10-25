import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, Link } from "@tanstack/react-router";
import { useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { Material, NewTask, TemplateSummary, Writer } from "@/api/types";
import { ModelAction } from "@/components/ai-writing";
import { Field, formClose } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { day, fileSize } from "@/format";

export function CreateTaskDialog({ ids, onClose }: { ids: number[]; onClose: () => void }) {
  const materials = useQuery({ queryKey: ["listMaterials"], queryFn: () => call("listMaterials", {}) });
  const templates = useQuery({ queryKey: ["listTemplates"], queryFn: () => call("listTemplates", {}) });
  const models = useQuery({ queryKey: ["listModelConfigs"], queryFn: () => call("listModelConfigs", {}) });
  const error = materials.error ?? templates.error ?? models.error;
  if (error)
    return (
      <Dialog open onOpenChange={(open) => !open && onClose()}>
        <DialogContent>
          <DialogHeader>新建任务</DialogHeader>
          <DialogBody>
            <p className="py-4 text-destructive">{errorMessage(error)}</p>
          </DialogBody>
        </DialogContent>
      </Dialog>
    );
  if (!materials.data || !templates.data || !models.data) return null;
  return (
    <TaskSteps
      ids={ids}
      materials={materials.data}
      templates={templates.data}
      modelId={models.data.find((m) => m.isDefault)?.id ?? null}
      onClose={onClose}
    />
  );
}
function TaskSteps({
  ids,
  materials,
  templates,
  modelId,
  onClose,
}: {
  ids: number[];
  materials: Material[];
  templates: TemplateSummary[];
  modelId: number | null;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [step, setStep] = useState(0);
  const [excluded, setExcluded] = useState<number[]>([]);
  const [form, setForm] = useState<NewTask>(() => {
    const date = new Date();
    return {
      name: `首封 ${date.getMonth() + 1}月${date.getDate()}日`,
      ids,
      writer: "ai",
      templateId: null,
      modelConfigId: modelId,
      references: materials.filter((m) => m.forWriting).map((m) => m.id),
      attachments: materials
        .filter(
          (m, i) =>
            m.attachByDefault && materials.findIndex((a) => a.attachByDefault && a.fileName === m.fileName) === i,
        )
        .map((m) => m.id),
    };
  });
  const requested = ids.filter((id) => !excluded.includes(id));
  const plan = useQuery({
    queryKey: [
      "planTask",
      requested,
      form.writer,
      form.templateId,
      form.references,
      form.attachments,
      form.modelConfigId,
    ],
    queryFn: () => call("planTask", { ...form, ids: requested }),
  });
  const eligible = plan.data?.mentors.filter((m) => m.reason === null) ?? [];
  const unavailable = plan.data?.mentors.filter((m) => m.reason !== null) ?? [];
  const contacted = eligible.filter((m) => m.contactCount > 0 || m.lastContactAt !== null);
  const create = useMutation({
    mutationFn: () => call("createTask", { ...form, ids: eligible.map((m) => m.id) }),
    onSuccess: ({ id }) => {
      void client.invalidateQueries({ queryKey: ["listTasks"] });
      onClose();
      void navigate({ to: "/tasks/$id", params: { id } });
    },
  });
  const attached = materials.filter((m) => form.attachments.includes(m.id));
  function toggle(key: "references" | "attachments", id: number, on: boolean) {
    setForm({ ...form, [key]: on ? [...form[key], id] : form[key].filter((i) => i !== id) });
  }
  const valid = eligible.length > 0 && (step !== 1 || form.writer === "ai" || form.templateId !== null);
  return (
    <Dialog open onOpenChange={formClose(true, create.isPending, onClose)}>
      <DialogContent className="w-[620px]">
        <DialogHeader>新建任务</DialogHeader>
        <div className="flex gap-4 px-6 pt-3 pb-2 text-sm phone:px-4">
          {["导师", "撰写方式", "发送"].map((s, i) => (
            <span key={s} className={i === step ? "font-medium text-brand-text" : "text-muted-foreground"}>
              {i + 1}　{s}
            </span>
          ))}
        </div>
        <DialogBody className="space-y-4 py-4 text-sm">
          {step === 0 && (
            <>
              <p className="text-base font-medium">将联系 {eligible.length} 位导师</p>
              {unavailable.length > 0 && (
                <details className="rounded-lg bg-muted p-3">
                  <summary className="cursor-pointer">有 {unavailable.length} 位不能加入</summary>
                  <ul className="mt-2 space-y-2 text-muted-foreground">
                    {unavailable.map((m) => (
                      <li key={m.id}>
                        {m.name}：{m.reason}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              {contacted.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 text-warning">
                  <p>其中 {contacted.length} 位联系过</p>
                  <Button
                    variant="quiet"
                    size="sm"
                    onClick={() => setExcluded([...excluded, ...contacted.map((m) => m.id)])}
                  >
                    全部移出
                  </Button>
                </div>
              )}
              <div className="max-h-72 space-y-[3px] overflow-y-auto">
                {eligible.map((m) => (
                  <div key={m.id} className="flex items-center gap-2 rounded-md bg-muted px-3 py-2">
                    <div className="min-w-0 grow">
                      <p>{m.name}</p>
                      {(m.contactCount > 0 || m.lastContactAt !== null) && (
                        <p className="text-xs text-warning">
                          联系 {m.contactCount} 次{m.lastContactAt !== null && `，最近 ${day(m.lastContactAt)}`}
                        </p>
                      )}
                    </div>
                    <Button variant="ghost" size="sm" onClick={() => setExcluded([...excluded, m.id])}>
                      移出
                    </Button>
                  </div>
                ))}
              </div>
              {eligible.length === 0 && (
                <p className="text-muted-foreground">没有可以加入任务的导师，请返回导师列表重新选择。</p>
              )}
            </>
          )}
          {step === 1 && (
            <>
              <div className="grid grid-cols-2 gap-2">
                {(
                  [
                    ["ai", "AI 逐一撰写"],
                    ["template", "使用模板"],
                  ] satisfies [Writer, string][]
                ).map(([value, label]) => (
                  <button
                    key={value}
                    onClick={() => setForm({ ...form, writer: value })}
                    aria-pressed={form.writer === value}
                    className="rounded-lg border border-border px-3 py-4 aria-pressed:border-primary aria-pressed:bg-selected"
                  >
                    {label}
                  </button>
                ))}
              </div>
              <Field id="task-template" label={form.writer === "ai" ? "底稿模板（可以不选）" : "模板"}>
                <NativeSelect
                  id="task-template"
                  value={form.templateId ?? ""}
                  onChange={(e) => setForm({ ...form, templateId: e.target.value ? Number(e.target.value) : null })}
                >
                  <option value="">{form.writer === "ai" ? "从头起草" : "请选择模板"}</option>
                  {templates.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
              <fieldset className="space-y-2">
                <legend className="mb-2 text-muted-foreground">参考材料</legend>
                {materials
                  .filter((m) => m.hasText)
                  .map((m) => (
                    <label className="flex items-center gap-2" key={m.id}>
                      <Checkbox
                        checked={form.references.includes(m.id)}
                        onCheckedChange={(on) => toggle("references", m.id, on)}
                      />
                      <span className="min-w-0 truncate">{m.fileName}</span>
                    </label>
                  ))}
              </fieldset>
              <fieldset className="space-y-2">
                <legend className="mb-2 text-muted-foreground">
                  附件 · {fileSize(attached.reduce((n, m) => n + m.size, 0))}
                </legend>
                {materials.map((m) => (
                  <label className="flex items-center gap-2" key={m.id}>
                    <Checkbox
                      checked={form.attachments.includes(m.id)}
                      disabled={!form.attachments.includes(m.id) && attached.some((a) => a.fileName === m.fileName)}
                      onCheckedChange={(on) => toggle("attachments", m.id, on)}
                    />
                    <span className="min-w-0 truncate">{m.fileName}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">{fileSize(m.size)}</span>
                  </label>
                ))}
              </fieldset>
              {materials.length === 0 && (
                <p className="text-muted-foreground">
                  尚未添加材料，可在
                  <Link to="/settings/materials" className="text-brand-text">
                    设置 › 个人 › 材料
                  </Link>
                  里添加。
                </p>
              )}
              {form.writer === "ai" && (
                <div className="space-y-1 rounded-lg bg-muted p-3">
                  <p>模型：{plan.data?.model ?? "尚未添加模型"}</p>
                  <p>
                    约 {((plan.data?.inputTokens ?? 0) + (plan.data?.outputTokens ?? 0)).toLocaleString()} Token
                    {plan.data?.cost != null && `，约 ${plan.data.cost.toFixed(2)} 元`}
                  </p>
                  <p className="text-xs text-muted-foreground">按字数估算，实际以服务商为准。</p>
                </div>
              )}
            </>
          )}
          {step === 2 && (
            <>
              <Field id="task-name" label="任务名称">
                <Input id="task-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </Field>
              <div className="space-y-2 rounded-lg bg-muted p-4">
                <p>
                  {eligible.length} 位导师 · {form.writer === "ai" ? "AI 逐一撰写" : "使用模板"}
                </p>
                <p>
                  {form.templateId ? templates.find((t) => t.id === form.templateId)?.name : "从头起草"} ·{" "}
                  {form.references.length} 份参考材料 · {attached.length} 个附件
                </p>
                <p className="font-medium">批准后立即发送</p>
                <p className="text-muted-foreground">
                  创建后先审核草稿。每封邮件经你批准后，按邮箱的余量和最短间隔依次发出。
                </p>
              </div>
            </>
          )}
          {plan.isFetching && <p className="text-muted-foreground">正在计算预计用量…</p>}
          {(plan.error ?? create.error) && (
            <p role="alert" className="text-destructive">
              {errorMessage(plan.error ?? create.error)}
            </p>
          )}
        </DialogBody>
        <DialogFooter className="justify-end">
          <Button variant="ghost" disabled={create.isPending} onClick={step === 0 ? onClose : () => setStep(step - 1)}>
            {step === 0 ? "取消" : "上一步"}
          </Button>
          {step < 2 ? (
            <Button disabled={!valid || plan.isFetching} onClick={() => setStep(step + 1)}>
              下一步
            </Button>
          ) : form.writer === "ai" ? (
            <ModelAction
              modelId={form.modelConfigId}
              disabled={create.isPending || !form.name.trim()}
              onWrite={() => create.mutate()}
            >
              创建任务
            </ModelAction>
          ) : (
            <Button disabled={create.isPending || !form.name.trim()} onClick={() => create.mutate()}>
              创建任务
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
