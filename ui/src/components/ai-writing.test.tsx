// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import type { ModelConfig } from "@/api/types";
import { ModelAction } from "./ai-writing";

const remote = vi.hoisted((): { models: ModelConfig[] } => ({ models: [] }));
vi.mock("@tanstack/react-router", () => ({ Link: ({ children }: { children: ReactNode }) => <span>{children}</span> }));
vi.mock("@/api/call", () => ({
  errorMessage: (error: Error) => error.message,
  call: async (op: string, input: { id: number }) => {
    if (op === "listModelConfigs") return structuredClone(remote.models);
    if (op === "listModelProviders") return [];
    if (op === "agreeModel") {
      const model = remote.models.find((m) => m.id === input.id);
      if (!model) throw new Error("missing model");
      model.agreed = true;
      return null;
    }
    throw new Error(`unexpected operation: ${op}`);
  },
}));
function button(name: string) {
  const node = Array.from(document.querySelectorAll("button")).find((b) => b.textContent === name);
  if (!node) throw new Error(`missing button: ${name}`);
  return node;
}
it("requires consent to both providers before resuming mixed crawl and enrichment work", async () => {
  remote.models = [1, 2].map((id) => ({
    id,
    name: null,
    model: "fixture",
    provider: "custom",
    baseUrl: `http://provider${id}.example/v1`,
    inputPrice: null,
    outputPrice: null,
    agreed: false,
    isDefault: id === 2,
  }));
  let resumed = false;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () =>
      root.render(
        <QueryClientProvider client={client}>
          <ModelAction
            modelIds={[1, 2]}
            disabled={false}
            onWrite={() => {
              resumed = true;
            }}
          >
            继续
          </ModelAction>
        </QueryClientProvider>,
      ),
    );
    await vi.waitFor(() => expect(button("继续").disabled).toBe(false));
    await act(async () => button("继续").click());
    expect(document.body.textContent).toContain("provider1.example");
    await act(async () => button("同意并继续").click());
    await vi.waitFor(() => expect(remote.models[0]?.agreed).toBe(true));
    expect(resumed).toBe(false);
    await vi.waitFor(() => expect(button("继续").disabled).toBe(false));
    await act(async () => button("继续").click());
    expect(document.body.textContent).toContain("provider2.example");
    await act(async () => button("同意并继续").click());
    await vi.waitFor(() => expect(resumed).toBe(true));
    expect(remote.models.every((m) => m.agreed)).toBe(true);
  } finally {
    await act(async () => root.unmount());
    client.clear();
    container.remove();
  }
});
