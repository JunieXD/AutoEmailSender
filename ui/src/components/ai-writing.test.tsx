// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import type { Letter, ModelConfig, ReplyTarget } from "@/api/types";
import { ModelAction, useReplyWritingTarget, useWriting } from "./ai-writing";

const remote = vi.hoisted(
  (): {
    models: ModelConfig[];
    write?: (input: Record<string, unknown>) => Promise<Letter>;
  } => ({ models: [] }),
);
vi.mock("@tanstack/react-router", () => ({ Link: ({ children }: { children: ReactNode }) => <span>{children}</span> }));
vi.mock("@/api/call", () => ({
  errorMessage: (error: Error) => error.message,
  call: async (op: string, input: Record<string, unknown>) => {
    if (op === "writeReply" && remote.write) return remote.write(input);
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

function WritingComposer({ fallback }: { fallback: ReplyTarget }) {
  const current = useReplyWritingTarget(7) ?? fallback;
  const writing = useWriting(7, undefined, undefined, current.messageId ?? "");
  return (
    <>
      <p>{current.subject}</p>
      <p>{writing.latest?.status}</p>
      <p>{writing.latest?.data?.bodyMd}</p>
      <button
        onClick={() => writing.start({ mentorId: 7, subject: "", bodyMd: "", attachments: [], replyTarget: current })}
      >
        起草
      </button>
      <button onClick={writing.forget}>取消</button>
    </>
  );
}

it("keeps an empty reply's AI target and result on return, and discards canceled late results", async () => {
  const target: ReplyTarget = {
    id: 1,
    messageId: "<selected@example.edu>",
    fromAddress: "mentor@example.edu",
    fromName: "王伟",
    subject: "面谈",
    sentAt: 1,
  };
  let complete: ((letter: Letter) => void) | undefined;
  remote.write = async (input) => {
    expect(input.replyTo).toBe(target.messageId);
    expect(input).not.toHaveProperty("replyTarget");
    return new Promise((resolve) => {
      complete = resolve;
    });
  };
  const client = new QueryClient();
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const render = (fallback: ReplyTarget | null) =>
    act(async () =>
      root.render(
        <QueryClientProvider client={client}>
          {fallback && <WritingComposer fallback={fallback} />}
        </QueryClientProvider>,
      ),
    );
  try {
    await render(target);
    await act(async () => button("起草").click());
    await vi.waitFor(() => expect(complete).toBeDefined());
    await render(null);
    await render({ ...target, id: 2, messageId: "<new@example.edu>", subject: "新来信" });
    await vi.waitFor(() => expect(container.textContent).toContain("pending"));
    expect(container.textContent).toContain("面谈");
    await act(async () => complete?.({ subject: "", bodyMd: "【请补充：时间】" }));
    await vi.waitFor(() => expect(container.textContent).toContain("【请补充：时间】"));
    await render(null);
    await render(target);
    expect(container.textContent).toContain("【请补充：时间】");
    complete = undefined;
    await act(async () => button("起草").click());
    await vi.waitFor(() => expect(complete).toBeDefined());
    await act(async () => button("取消").click());
    await act(async () => complete?.({ subject: "", bodyMd: "迟到结果" }));
    await render(null);
    await render(target);
    expect(container.textContent).not.toContain("迟到结果");
    expect(container.textContent).not.toContain("pending");
  } finally {
    await act(async () => root.unmount());
    client.clear();
    container.remove();
    remote.write = undefined;
  }
});
