import { describe, expect, it, vi } from "vitest";

import { createBufferedDelivery } from "../src/preload/agent-ui-handoff-buffer.js";

describe("Agent UI handoff preload buffer", () => {
  it("delivers a buffered value to the live StrictMode resubscription", () => {
    const microtasks: Array<() => void> = [];
    const delivery = createBufferedDelivery<string>((callback) => {
      microtasks.push(callback);
    });
    const first = vi.fn();
    const second = vi.fn();

    delivery.publish("handoff");
    const unsubscribeFirst = delivery.subscribe(first);
    unsubscribeFirst();
    delivery.subscribe(second);
    microtasks.splice(0).forEach((callback) => callback());

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledOnce();
    expect(second).toHaveBeenCalledWith("handoff");
  });

  it("keeps the latest value buffered until a subscriber exists", () => {
    const microtasks: Array<() => void> = [];
    const delivery = createBufferedDelivery<number>((callback) => {
      microtasks.push(callback);
    });
    const callback = vi.fn();

    delivery.publish(1);
    delivery.publish(2);
    delivery.subscribe(callback);
    microtasks.splice(0).forEach((task) => task());

    expect(callback).toHaveBeenCalledTimes(1);
    expect(callback).toHaveBeenCalledWith(2);
  });
});
