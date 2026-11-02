// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import type { Tag } from "@/api/types";
import { TagPicker } from "./tags";
import { TodoEditor } from "./todos";

const remote = vi.hoisted((): { renames: string[] } => ({ renames: [] }));
vi.mock("@/api/call", () => ({
  errorMessage: (error: Error) => error.message,
  call: async (op: string, input: { name: string }) => {
    if (op === "listTags") return [tag];
    if (op === "renameTag") {
      remote.renames.push(input.name);
      return null;
    }
    throw new Error(`unexpected operation: ${op}`);
  },
}));

const tag: Tag = { id: 1, name: "原标签", color: "#2563eb" };

function mount(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  return {
    render: () => act(async () => root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)),
    async close() {
      await act(async () => root.unmount());
      client.clear();
      container.remove();
    },
  };
}

function type(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function press(input: HTMLInputElement, key: string, init: KeyboardEventInit = {}) {
  input.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, ...init }));
}

function button(name: string) {
  const node = Array.from(document.querySelectorAll("button")).find((b) => b.textContent?.trim() === name);
  if (!node) throw new Error(`missing button: ${name}`);
  return node;
}

it("leaves Enter and Esc to the input method while it picks characters for a tag name", async () => {
  remote.renames = [];
  const view = mount(<TagPicker mentorId={7} selected={[]} />);
  try {
    await view.render();
    await act(async () => button("编辑").click());
    await vi.waitFor(() => button("管理标签"));
    await act(async () => button("管理标签").click());
    const name = await vi.waitFor(() => {
      const input = document.querySelector<HTMLInputElement>('input[aria-label="标签名称"]');
      if (!input) throw new Error("no tag name");
      return input;
    });
    await act(async () => type(name, "新标签 shenqing"));
    await act(async () => press(name, "Enter", { isComposing: true }));
    // WebKit's keydown that ends the composition.
    await act(async () => press(name, "Enter", { keyCode: 229 }));
    await act(async () => press(name, "Escape", { isComposing: true }));
    expect(remote.renames).toEqual([]);
    expect(name.value).toBe("新标签 shenqing");
    await act(async () => type(name, "新标签申请"));
    await act(async () => press(name, "Enter"));
    await vi.waitFor(() => expect(remote.renames).toEqual(["新标签申请"]));
  } finally {
    await view.close();
  }
});

it("keeps a to-do being typed when Esc ends the input method's composition", async () => {
  const cancel = vi.fn();
  const view = mount(<TodoEditor pending={false} error={null} onSave={() => undefined} onCancel={cancel} />);
  try {
    await view.render();
    const text = document.querySelector<HTMLInputElement>('input[aria-label="待办内容"]');
    if (!text) throw new Error("no to-do");
    await act(async () => type(text, "把成绩单 fa"));
    await act(async () => press(text, "Escape", { isComposing: true }));
    expect(cancel).not.toHaveBeenCalled();
    await act(async () => press(text, "Escape"));
    expect(cancel).toHaveBeenCalledOnce();
  } finally {
    await view.close();
  }
});
