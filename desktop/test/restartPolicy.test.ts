import { describe, expect, it } from "vitest";
import { RestartPolicy } from "../src/main/backend/restart-policy.js";

describe("runtime restart policy", () => {
  it("uses exponential backoff, a 30 second cap, and a five-minute circuit window", () => {
    let now = 0;
    const policy = new RestartPolicy({ now: () => now, random: () => 0.5 });
    const decisions = [];

    for (let index = 0; index < 6; index += 1) {
      decisions.push(policy.recordFailure());
      now += 10;
    }

    expect(decisions.map((decision) => decision.backoffDelayMs)).toEqual([
      1_000,
      2_000,
      4_000,
      8_000,
      16_000,
      30_000,
    ]);
    expect(decisions[3].circuitOpenUntil).toBeNull();
    expect(decisions[4].delayMs).toBe(60_000);
    expect(decisions[4].failuresInWindow).toBe(5);
    expect(decisions[5].delayMs).toBe(60_000);
  });

  it("applies bounded jitter without exceeding the 30 second cap", () => {
    let random = 0;
    const policy = new RestartPolicy({ random: () => random });

    expect(policy.recordFailure().backoffDelayMs).toBe(800);
    random = 1;
    expect(policy.recordFailure().backoffDelayMs).toBe(2_400);
    policy.recordFailure();
    policy.recordFailure();
    expect(policy.recordFailure().backoffDelayMs).toBe(19_200);
    expect(policy.recordFailure().backoffDelayMs).toBe(30_000);
  });

  it("prunes old failures and resets only after a stable five-minute run", () => {
    let now = 0;
    const policy = new RestartPolicy({ now: () => now, random: () => 0.5 });

    policy.recordFailure();
    policy.recordFailure();
    expect(policy.resetAfterStableRun(now)).toBe(false);

    now = 5 * 60_000;
    expect(policy.resetAfterStableRun(0)).toBe(true);
    expect(policy.recordFailure()).toMatchObject({
      backoffDelayMs: 1_000,
      consecutiveFailures: 1,
      failuresInWindow: 1,
      circuitOpenUntil: null,
    });

    now += 5 * 60_000 + 1;
    expect(policy.recordFailure().failuresInWindow).toBe(1);
  });

  it("closes an expired circuit before evaluating a later isolated failure", () => {
    let now = 0;
    const policy = new RestartPolicy({ now: () => now, random: () => 0.5 });
    for (let index = 0; index < 5; index += 1) {
      policy.recordFailure();
    }
    now = 6 * 60_000;

    const decision = policy.recordFailure();

    expect(decision.failuresInWindow).toBe(1);
    expect(decision.circuitOpenUntil).toBeNull();
    expect(decision.delayMs).toBe(30_000);
  });
});
