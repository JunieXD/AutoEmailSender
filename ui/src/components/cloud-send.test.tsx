// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import type { CloudHandOff, CloudSendPlanned, CloudSendPreview, CloudSendPreviewInput } from "@/api/types";
import { CloudSendButton } from "./cloud-send";

const DAY = 86_400_000;
const AT = new Date(2027, 0, 15, 9).getTime();
const planned = (id: number, name: string, at: number): CloudSendPlanned => ({
  id,
  mentorName: name,
  taskId: 1,
  taskName: "春季申请",
  plannedAt: at,
  mailboxId: 1,
  at,
});
const remote = vi.hoisted(() => {
  const handed: unknown[] = [];
  const previews: unknown[] = [];
  return { handed, previews };
});
vi.mock("@tanstack/react-router", async (original) => ({
  ...(await original<typeof import("@tanstack/react-router")>()),
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}));
vi.mock("@/api/call", () => ({
  errorMessage: (error: Error) => error.message,
  call: async (op: string, input: CloudSendPreviewInput | CloudHandOff) => {
    if (op === "cloudSendPreview" && !("consents" in input)) {
      remote.previews.push(input);
      // Without the first, the second takes its place.
      const emails =
        input.emails !== undefined ? [planned(2, "李娜", AT)] : [planned(1, "王伟", AT), planned(2, "李娜", AT + DAY)];
      const preview: CloudSendPreview = {
        blocked: null,
        emails,
        unfit: [{ id: 3, mentorName: "张敏", taskName: null, reason: "follow_up" }],
        mailboxes: [{ id: 1, address: "me@qq.com", needsConsent: true }],
        size: 2048,
      };
      return preview;
    }
    if (op === "handOffToCloud" && "consents" in input) {
      remote.handed.push(input);
      return { handed: input.emails.map((e) => e.id), kept: [], loginFailed: [] };
    }
    throw new Error(`unexpected operation: ${op}`);
  },
}));

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const container = document.createElement("div");
document.body.append(container);
const root = createRoot(container);
afterEach(async () => {
  await act(async () => root.unmount());
  client.clear();
  container.remove();
});

function button(text: string): HTMLButtonElement | undefined {
  return Array.from(document.querySelectorAll("button")).find((b) => b.textContent === text);
}
function label(text: string): HTMLLabelElement | undefined {
  return Array.from(document.querySelectorAll("label")).find((l) => l.textContent?.includes(text));
}

it("hands over the ticked emails as planned again, once the secret is agreed to", async () => {
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <CloudSendButton />
      </QueryClientProvider>,
    );
  });
  await act(async () => button("交给云端发送")?.click());
  await vi.waitFor(() => expect(document.body.textContent).toContain("王伟"));
  expect(document.body.textContent).toContain("不能交的 1 封：追问 1 封");
  expect(document.body.textContent).toContain("共 2 KB");
  // Not agreed to yet.
  expect(button("交给云端")?.disabled).toBe(true);

  await act(async () => label("王伟")?.click());
  await vi.waitFor(() => expect(remote.previews).toContainEqual({ emails: [2] }));
  // Planned again without the first, the second goes at its time.
  await vi.waitFor(() => expect(label("李娜")?.textContent).toContain("1月15日 09:00 从 me@qq.com 发出"));
  await vi.waitFor(() => expect(label("同意把 me@qq.com 的授权码")).toBeDefined());
  await act(async () => label("同意把 me@qq.com 的授权码")?.click());
  await vi.waitFor(() => expect(button("交给云端")?.disabled).toBe(false));
  await act(async () => button("交给云端")?.click());
  await vi.waitFor(() => expect(document.body.textContent).toContain("已交给云端 1 封"));
  expect(remote.handed).toEqual([{ emails: [{ id: 2, mailboxId: 1, at: AT }], consents: [1] }]);
});
