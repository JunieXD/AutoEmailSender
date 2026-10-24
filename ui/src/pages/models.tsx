import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { ModelConfig, ModelProvider } from "@/api/types";
import { Confirm } from "@/components/batch-bar";
import { ModelDialog, useModelProviders } from "@/components/model-dialog";
import { DefaultMark } from "@/components/default-mark";
import { Button } from "@/components/ui/button";
import { Section } from "./settings";

/** Settings › AI › 模型 (docs/ui.md#交互). */
export function ModelsSection() {
  const configs = useQuery({ queryKey: ["listModelConfigs"], queryFn: () => call("listModelConfigs", {}) });
  const providers = useModelProviders();
  const [adding, setAdding] = useState(false);
  const error = configs.error ?? providers.error;
  return (
    <Section title="模型">
      <p className="text-sm text-muted-foreground">
        分析匹配度、写邮件草稿用的 AI 模型，可以添加多个。API Key 只存在这台电脑上，只发给对应的服务商。
      </p>
      {error && <p className="text-sm text-destructive">模型没能加载：{errorMessage(error)}</p>}
      {configs.data && providers.data && (
        <>
          {configs.data.length > 0 && (
            <ul className="flex flex-col divide-y divide-border">
              {configs.data.map((c) => (
                <ModelRow key={c.id} config={c} providers={providers.data} />
              ))}
            </ul>
          )}
          <div>
            <Button onClick={() => setAdding(true)}>添加模型</Button>
          </div>
          {adding && <ModelDialog config={null} providers={providers.data} onClose={() => setAdding(false)} />}
        </>
      )}
    </Section>
  );
}

function ModelRow({ config: c, providers }: { config: ModelConfig; providers: ModelProvider[] }) {
  const [editing, setEditing] = useState(false);
  const queryClient = useQueryClient();
  const test = useMutation({
    mutationFn: () => call("testModel", { ...c, id: c.id, name: "", baseUrl: c.baseUrl ?? "", apiKey: "" }),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["listModelConfigs"] });
  const remove = useMutation({ mutationFn: () => call("deleteModelConfig", { id: c.id }), onSuccess: refresh });
  const makeDefault = useMutation({ mutationFn: () => call("setDefaultModel", { id: c.id }), onSuccess: refresh });
  // A custom configuration is known by its address.
  const source = providers.find((p) => p.id === c.provider)?.name ?? c.baseUrl;
  return (
    <li className="flex flex-col gap-1.5 py-3">
      <div className="flex items-center gap-2 phone:flex-col phone:items-start">
        <div className="min-w-0 grow">
          <p className="flex items-center gap-2 font-medium">
            <span className="truncate">{c.name ?? c.model}</span>
            {c.isDefault && <DefaultMark />}
          </p>
          <p className="truncate text-sm text-muted-foreground">{c.name ? `${source}，${c.model}` : source}</p>
        </div>
        <div className="flex gap-1">
          <Button variant="quiet" disabled={test.isPending} onClick={() => test.mutate()}>
            测试
          </Button>
          {!c.isDefault && (
            <Button variant="ghost" disabled={makeDefault.isPending} onClick={() => makeDefault.mutate()}>
              设为默认
            </Button>
          )}
          <Button variant="ghost" onClick={() => setEditing(true)}>
            编辑
          </Button>
          <Confirm
            trigger={
              <Button variant="ghost" disabled={remove.isPending}>
                删除
              </Button>
            }
            message="删除这个模型？用过它的记录里还保留模型名。"
            action="删除"
            side="bottom"
            onConfirm={() => remove.mutate()}
          />
        </div>
      </div>
      <p className="text-sm empty:hidden" aria-live="polite">
        {test.isPending && <span className="text-muted-foreground">正在测试…</span>}
        {test.isSuccess && <span className="text-muted-foreground">模型回复正常。</span>}
        {test.isError && <span className="text-destructive">{errorMessage(test.error)}</span>}
        {remove.isError && <span className="text-destructive">{errorMessage(remove.error)}</span>}
        {makeDefault.isError && <span className="text-destructive">{errorMessage(makeDefault.error)}</span>}
      </p>
      {editing && (
        <ModelDialog
          config={c}
          providers={providers}
          // A test of the old settings says nothing about the new ones.
          onSaved={() => test.reset()}
          onClose={() => setEditing(false)}
        />
      )}
    </li>
  );
}
