// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import type { PlanEmail } from "@/api/types";
import { SendingPlan } from "./sending-plan";

const remote = vi.hoisted(() => ({ confirmed: false }));
vi.mock("@tanstack/react-router", async (original) => ({
  ...(await original<typeof import("@tanstack/react-router")>()),
  useSearch: () => ({}),
  useNavigate: () => () => undefined,
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}));
vi.mock("@/api/call", () => ({
  errorMessage: (error: Error) => error.message,
  call: async (op: string, input: { column?: string }) => {
    if (op === "confirmUnsentEmail") {
      remote.confirmed = true;
      return null;
    }
    if (op === "sendingPlan") {
      const emails = input.column === "pending" && !remote.confirmed ? [email] : [];
      return { emails, total: emails.length };
    }
    throw new Error(`unexpected operation: ${op}`);
  },
}));

const email: PlanEmail = {
  id: 1,
  kind: "first",
  replyTo: null,
  mentorId: 1,
  name: "王伟",
  subject: "申请硕士",
  taskId: null,
  taskName: null,
  state: "maybe_sent",
  resendOf: null,
  plannedAt: 1,
  latestAt: null,
  sentAt: 1,
  fromAddress: "me@example.com",
  error: "发送途中应用退出，无法确定是否已发出。",
  cancelReason: "导师已移入回收站",
  waiting: null,
};
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const container = document.createElement("div");
document.body.append(container);
const root = createRoot(container);
afterEach(async () => {
  await act(async () => root.unmount());
  client.clear();
  container.remove();
});

it("shows cancellation and requires confirmation before finishing uncertain mail without resending", async () => {
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <SendingPlan />
      </QueryClientProvider>,
    );
  });
  await vi.waitFor(() => expect(container.textContent).toContain("导师已移入回收站"));
  expect(container.textContent).not.toContain("重新发送");
  const trigger = Array.from(container.querySelectorAll("button")).find(
    (button) => button.textContent === "确认没发出",
  );
  expect(trigger).toBeDefined();
  await act(async () => trigger?.click());
  const popup = document.querySelector('[data-slot="popover-content"]');
  expect(popup?.textContent).toContain("不再发送");
  expect(container.textContent).toContain("申请硕士");
  const confirm = Array.from(popup?.querySelectorAll("button") ?? []).find(
    (button) => button.textContent === "确认没发出",
  );
  expect(confirm).toBeDefined();
  await act(async () => confirm?.click());
  await vi.waitFor(() => expect(container.textContent).not.toContain("申请硕士"));
  expect(container.textContent).toContain("当前没有待处理邮件");
});
