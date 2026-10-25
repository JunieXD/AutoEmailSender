// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { useScheduleCapacity } from "@/components/schedule";
import type { Schedule } from "@/api/types";

const capacityCall = vi.hoisted(() => vi.fn());
vi.mock("@/api/call", () => ({ call: capacityCall, errorMessage: String }));

const schedule: Schedule = {
  kind: "window",
  dates: ["2026-10-26", "2026-10-27"],
  start: "09:00",
  end: "11:00",
  perWindow: 1,
};
const now = Date.UTC(2026, 9, 26, 10);
let client: QueryClient;
let root: Root;
let container: HTMLDivElement;

function CapacityForm() {
  const capacity = useScheduleCapacity(schedule);
  return (
    <>
      <output>{capacity.data?.emails ?? "读取中"}</output>
      <button disabled={!capacity.data || capacity.data.emails < 2}>创建任务</button>
    </>
  );
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.useFakeTimers();
  vi.setSystemTime(now);
  capacityCall.mockReset();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  client.clear();
  container.remove();
  vi.useRealTimers();
});

test("capacity changes at the next window boundary and refreshes on focus without polling", async () => {
  capacityCall
    .mockResolvedValueOnce({ windows: 2, emails: 2, nextChangeAt: now + 60_000 })
    .mockResolvedValueOnce({ windows: 1, emails: 1, nextChangeAt: now + 86_400_000 })
    .mockResolvedValue({ windows: 0, emails: 0, nextChangeAt: null });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <CapacityForm />
      </QueryClientProvider>,
    );
  });
  await advance(0);
  expect(container.querySelector("output")?.textContent).toBe("2");
  expect(container.querySelector("button")?.disabled).toBe(false);
  await advance(59_000);
  expect(capacityCall).toHaveBeenCalledTimes(1);
  await advance(1_000);
  expect(container.querySelector("output")?.textContent).toBe("1");
  expect(container.querySelector("button")?.disabled).toBe(true);
  expect(capacityCall).toHaveBeenCalledTimes(2);
  await act(async () => {
    window.dispatchEvent(new Event("focus"));
  });
  await advance(0);
  expect(container.querySelector("output")?.textContent).toBe("0");
  await advance(86_400_000);
  expect(capacityCall).toHaveBeenCalledTimes(3);
});
