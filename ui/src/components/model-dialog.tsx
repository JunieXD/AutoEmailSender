import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { openUrl } from "@tauri-apps/plugin-opener";
import { useRef, useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { ModelConfig, ModelProvider, ModelSource, ModelTest, NewModelConfig } from "@/api/types";
import { formClose, Field, TestResult } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";

/** The provider table from the core; it never changes while the app runs. */
export function useModelProviders() {
  return useQuery({
    queryKey: ["listModelProviders"],
    queryFn: () => call("listModelProviders", {}),
    staleTime: Infinity,
  });
}

const CUSTOM = "custom";

const EMPTY: NewModelConfig = { name: "", provider: "", baseUrl: "", model: "", apiKey: "" };

function formOf(c: ModelConfig): NewModelConfig {
  return { name: c.name ?? "", provider: c.provider, baseUrl: c.baseUrl ?? "", model: c.model, apiKey: "" };
}

/** Adding or editing a model configuration (docs/ui.md#交互). Mounted while open. */
export function ModelDialog({
  config,
  providers,
  onSaved,
  onClose,
}: {
  config: ModelConfig | null;
  providers: ModelProvider[];
  /** After saving, before closing. */
  onSaved?: () => void;
  onClose: () => void;
}) {
  const [initial] = useState(() => (config ? formOf(config) : EMPTY));
  const [form, setForm] = useState(initial);
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: (f: NewModelConfig) =>
      config ? call("updateModelConfig", { ...f, id: config.id }) : call("addModelConfig", f),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["listModelConfigs"] });
      onSaved?.();
      onClose();
    },
  });
  const test = useMutation({ mutationFn: (t: ModelTest) => call("testModel", t) });
  const models = useMutation({ mutationFn: (s: ModelSource) => call("listProviderModels", s) });
  const providerRef = useRef<HTMLSelectElement>(null);

  const provider = providers.find((p) => p.id === form.provider);
  // The core only uses the saved key where it was saved for.
  const keySaved =
    config !== null &&
    form.provider === initial.provider &&
    (form.provider !== CUSTOM || form.baseUrl === initial.baseUrl);
  const reachable =
    (provider !== undefined || (form.provider === CUSTOM && form.baseUrl.trim() !== "")) &&
    (keySaved || form.provider === CUSTOM || form.apiKey.trim() !== "");
  const complete = reachable && form.model.trim() !== "";
  const dirty = JSON.stringify(form) !== JSON.stringify(initial);

  /** Changes the form; an earlier test no longer applies, nor a list from another provider or key. */
  function edit(next: NewModelConfig) {
    setForm(next);
    test.reset();
    if (next.provider !== form.provider || next.baseUrl !== form.baseUrl || next.apiKey !== form.apiKey) {
      models.reset();
    }
  }

  const pending = save.isPending || test.isPending;
  return (
    <Dialog open onOpenChange={formClose(dirty, save.isPending, onClose)}>
      <DialogContent initialFocus={providerRef}>
        <form
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate(form);
          }}
        >
          <DialogHeader>{config ? "编辑模型" : "添加模型"}</DialogHeader>
          <DialogBody>
            <fieldset disabled={save.isPending} className="flex min-w-0 flex-col gap-3.5">
              <Field label="服务商" id="model-provider">
                <NativeSelect
                  ref={providerRef}
                  id="model-provider"
                  value={form.provider}
                  onChange={(e) => edit({ ...form, provider: e.target.value })}
                >
                  <NativeSelectOption value="" disabled>
                    选择服务商
                  </NativeSelectOption>
                  {providers.map((p) => (
                    <NativeSelectOption key={p.id} value={p.id}>
                      {p.name}
                    </NativeSelectOption>
                  ))}
                  <NativeSelectOption value={CUSTOM}>其他（OpenAI 兼容接口）</NativeSelectOption>
                </NativeSelect>
              </Field>
              {form.provider === CUSTOM && (
                <Field label="接口地址" id="model-base-url">
                  <Input
                    id="model-base-url"
                    type="url"
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="https://example.com/v1"
                    value={form.baseUrl}
                    onChange={(e) => edit({ ...form, baseUrl: e.target.value })}
                  />
                </Field>
              )}
              <Field label="API Key" id="model-api-key">
                <Input
                  id="model-api-key"
                  type="password"
                  autoComplete="off"
                  placeholder={keySaved ? "不修改就留空" : form.provider === CUSTOM ? "本机的模型可以不填" : undefined}
                  value={form.apiKey}
                  onChange={(e) => edit({ ...form, apiKey: e.target.value })}
                />
                {provider && (
                  <button
                    type="button"
                    className="mt-1.5 text-sm text-brand-text hover:underline"
                    onClick={() => void openUrl(provider.keyUrl)}
                  >
                    获取 API Key
                  </button>
                )}
              </Field>
              <Field label="模型名" id="model-model">
                <div className="flex gap-2">
                  <Input
                    id="model-model"
                    autoComplete="off"
                    spellCheck={false}
                    list="model-models"
                    value={form.model}
                    onChange={(e) => edit({ ...form, model: e.target.value })}
                  />
                  <Button
                    type="button"
                    variant="quiet"
                    disabled={!reachable || models.isPending}
                    onClick={() => models.mutate({ ...form, id: config?.id })}
                  >
                    获取列表
                  </Button>
                </div>
                <datalist id="model-models">
                  {models.data?.map((m) => (
                    <option key={m} value={m} />
                  ))}
                </datalist>
                <p className="mt-1.5 text-sm empty:hidden" aria-live="polite">
                  {models.isPending && <span className="text-muted-foreground">正在获取模型列表…</span>}
                  {models.isSuccess && (
                    <span className="text-muted-foreground">
                      {models.data.length > 0
                        ? `获取到 ${models.data.length} 个模型，点输入框选择或输入名字筛选。`
                        : "服务商没有列出模型，请手动填写。"}
                    </span>
                  )}
                  {models.isError && <span className="text-destructive">{errorMessage(models.error)}</span>}
                </p>
              </Field>
              <Field label="名称" id="model-name">
                <Input
                  id="model-name"
                  autoComplete="off"
                  placeholder="可以不填，不填时显示模型名"
                  value={form.name}
                  onChange={(e) => edit({ ...form, name: e.target.value })}
                />
              </Field>
            </fieldset>
          </DialogBody>
          {test.status !== "idle" && (
            <div className="px-6 pt-3 text-sm phone:px-4" aria-live="polite">
              <TestResult label="测试" mutation={test} />
            </div>
          )}
          <DialogFooter>
            <p className="grow text-sm text-destructive" role="alert">
              {save.isError && errorMessage(save.error)}
            </p>
            <Button
              type="button"
              variant="quiet"
              disabled={pending || !complete}
              onClick={() => test.mutate({ ...form, id: config?.id })}
            >
              测试
            </Button>
            <DialogClose render={<Button variant="ghost" />}>取消</DialogClose>
            <Button type="submit" disabled={save.isPending || !complete}>
              {config ? "保存" : "添加"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
