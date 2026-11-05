// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import type { Plus } from "@/api/types";
import { AccountSection } from "./account";

const remote = vi.hoisted(() => ({ getPlus: vi.fn<() => Promise<Plus>>(), redeem: vi.fn() }));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}));
vi.mock("@/api/call", () => ({
  call: async (op: string) => {
    if (op === "getAccount") return { kind: "loggedIn", email: "lisi@163.com", termsOutdated: false };
    if (op === "getPlus") return remote.getPlus();
    if (op === "getPlusItems") return { tiers: [], packs: [], latest: AFTER, refunded: false };
    if (op === "getInvite") return { status: "ineligible" };
    if (op === "redeemCode") {
      remote.redeem();
      return { days: 7, plusUntil: AFTER };
    }
    throw new Error(`unexpected operation: ${op}`);
  },
  errorMessage: (error: Error) => error.message,
}));

const BEFORE = new Date(2030, 0, 1, 12).getTime();
const AFTER = new Date(2030, 0, 8, 12).getTime();

function button(text: string) {
  return Array.from(document.querySelectorAll("button")).find((item) => item.textContent === text);
}

it("shows the period after a redeem even when a read started before it answers last", async () => {
  let answerOldRead!: (plus: Plus) => void;
  remote.getPlus
    .mockReset()
    .mockReturnValueOnce(new Promise((resolve) => (answerOldRead = resolve)))
    .mockResolvedValue({ plusUntil: AFTER });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () =>
      root.render(
        <QueryClientProvider client={client}>
          <AccountSection />
        </QueryClientProvider>,
      ),
    );
    await vi.waitFor(() => expect(button("有兑换码？")).toBeDefined());
    await act(async () => button("有兑换码？")?.click());
    await act(async () => {
      const input = document.querySelector<HTMLInputElement>("#redeem-code");
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, "ABCD-EFGH-JKLM");
      input?.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => button("兑换")?.click());
    await vi.waitFor(() => expect(remote.redeem).toHaveBeenCalled());
    await act(async () => answerOldRead({ plusUntil: BEFORE }));
    await vi.waitFor(() => expect(container.textContent).toContain("兑换成功，Plus 增加 7 天。"));
    expect(container.textContent).toContain("1月8日");
    expect(container.textContent).not.toContain("1月1日");
  } finally {
    await act(async () => root.unmount());
    client.clear();
    container.remove();
  }
});
