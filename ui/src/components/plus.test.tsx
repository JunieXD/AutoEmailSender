// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import type { OrderState, Tier } from "@/api/types";
import { PayDialog } from "./plus";

const remote = vi.hoisted(() => ({
  ordered: vi.fn(),
  opened: vi.fn(),
  order: vi.fn<() => Promise<OrderState>>(),
}));
vi.mock("@tauri-apps/plugin-opener", () => ({ openUrl: async (url: string) => remote.opened(url) }));
vi.mock("@/api/call", () => ({
  call: async (op: string, input: unknown) => {
    if (op === "createPlusOrder") {
      remote.ordered(input);
      return { order: 7, payUrl: "https://pay.example/7" };
    }
    if (op === "getPlusOrder") return remote.order();
    if (op === "getPlus") return { plusUntil: UNTIL };
    if (op === "getPlusItems" || op === "getTracking") return null;
    throw new Error(`unexpected operation: ${op}`);
  },
  errorMessage: (error: Error) => error.message,
}));

const UNTIL = new Date(2030, 1, 1, 12).getTime();
const tier: Tier = { item: "1m", duration: { months: 1 }, price: 1880, until: UNTIL, available: true };
const quota = { used: 0, limit: 500, packLeft: 0, packExpiresAt: null };

function button(text: string) {
  return Array.from(document.querySelectorAll("button")).find((item) => item.textContent === text);
}

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = "";
});

it("orders only after the Plus terms are agreed to, then waits for the payment to arrive", async () => {
  vi.useFakeTimers();
  remote.order.mockResolvedValueOnce({ state: "pending" }).mockResolvedValue({
    state: "applied",
    plusUntil: UNTIL,
    tracking: quota,
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const root = createRoot(document.body.appendChild(document.createElement("div")));
  try {
    await act(async () =>
      root.render(
        <QueryClientProvider client={client}>
          <PayDialog email="lisi@163.com" purchase={{ tier }} onClose={() => {}} />
        </QueryClientProvider>,
      ),
    );
    expect(button("支付宝")?.disabled).toBe(true);
    await act(async () => document.querySelector("label")?.click());
    await act(async () => button("支付宝")?.click());
    await vi.waitFor(() => expect(document.body.textContent).toContain("请在浏览器中完成付款"));
    expect(remote.ordered).toHaveBeenCalledWith({ item: "1m", method: "alipay" });
    expect(remote.opened).toHaveBeenCalledWith("https://pay.example/7");

    await act(async () => vi.advanceTimersByTimeAsync(3000));
    await vi.waitFor(() => expect(document.body.textContent).toContain("已开通，Plus 到2030年2月1日。"));
    expect(button("完成")).toBeDefined();
  } finally {
    await act(async () => root.unmount());
    client.clear();
  }
});

it("stops waiting after 15 minutes without a payment", async () => {
  vi.useFakeTimers();
  remote.order.mockReset().mockResolvedValue({ state: "pending" });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const root = createRoot(document.body.appendChild(document.createElement("div")));
  try {
    await act(async () =>
      root.render(
        <QueryClientProvider client={client}>
          <PayDialog email="lisi@163.com" purchase={{ tier }} onClose={() => {}} />
        </QueryClientProvider>,
      ),
    );
    await act(async () => document.querySelector("label")?.click());
    await act(async () => button("微信支付")?.click());
    await act(async () => vi.advanceTimersByTimeAsync(15 * 60_000));
    await vi.waitFor(() => expect(document.body.textContent).toContain("尚未收到付款。如已付款，稍后会自动开通。"));
    const asked = remote.order.mock.calls.length;
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(remote.order.mock.calls.length).toBe(asked);
  } finally {
    await act(async () => root.unmount());
    client.clear();
  }
});
