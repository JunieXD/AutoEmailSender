export type RestartPolicyOptions = {
  now?: () => number;
  random?: () => number;
  initialDelayMs?: number;
  maximumDelayMs?: number;
  jitterRatio?: number;
  circuitFailureLimit?: number;
  circuitWindowMs?: number;
  circuitOpenMs?: number;
  stableResetMs?: number;
};

export type RestartDecision = {
  delayMs: number;
  backoffDelayMs: number;
  consecutiveFailures: number;
  failuresInWindow: number;
  circuitOpenUntil: number | null;
};

export class RestartPolicy {
  readonly #now: () => number;
  readonly #random: () => number;
  readonly #initialDelayMs: number;
  readonly #maximumDelayMs: number;
  readonly #jitterRatio: number;
  readonly #circuitFailureLimit: number;
  readonly #circuitWindowMs: number;
  readonly #circuitOpenMs: number;
  readonly #stableResetMs: number;
  #consecutiveFailures = 0;
  #failureTimes: number[] = [];
  #circuitOpenUntil: number | null = null;

  constructor(options: RestartPolicyOptions = {}) {
    this.#now = options.now ?? Date.now;
    this.#random = options.random ?? Math.random;
    this.#initialDelayMs = options.initialDelayMs ?? 1_000;
    this.#maximumDelayMs = options.maximumDelayMs ?? 30_000;
    this.#jitterRatio = options.jitterRatio ?? 0.2;
    this.#circuitFailureLimit = options.circuitFailureLimit ?? 5;
    this.#circuitWindowMs = options.circuitWindowMs ?? 5 * 60_000;
    this.#circuitOpenMs = options.circuitOpenMs ?? 60_000;
    this.#stableResetMs = options.stableResetMs ?? 5 * 60_000;
  }

  recordFailure(): RestartDecision {
    const now = this.#now();
    if (this.#circuitOpenUntil !== null && this.#circuitOpenUntil <= now) {
      this.#circuitOpenUntil = null;
    }
    this.#failureTimes = this.#failureTimes.filter(
      (failedAt) => now - failedAt <= this.#circuitWindowMs,
    );
    this.#failureTimes.push(now);

    const unjittered = Math.min(
      this.#maximumDelayMs,
      this.#initialDelayMs * (2 ** this.#consecutiveFailures),
    );
    this.#consecutiveFailures += 1;
    const random = Math.max(0, Math.min(1, this.#random()));
    const jitterFactor = 1 - this.#jitterRatio + (2 * this.#jitterRatio * random);
    const backoffDelayMs = Math.min(
      this.#maximumDelayMs,
      Math.max(0, Math.round(unjittered * jitterFactor)),
    );

    if (this.#failureTimes.length >= this.#circuitFailureLimit) {
      this.#circuitOpenUntil = Math.max(
        this.#circuitOpenUntil ?? 0,
        now + this.#circuitOpenMs,
      );
    }
    const circuitDelayMs = Math.max(0, (this.#circuitOpenUntil ?? now) - now);
    return {
      delayMs: Math.max(backoffDelayMs, circuitDelayMs),
      backoffDelayMs,
      consecutiveFailures: this.#consecutiveFailures,
      failuresInWindow: this.#failureTimes.length,
      circuitOpenUntil: this.#circuitOpenUntil,
    };
  }

  resetAfterStableRun(startedAt: number): boolean {
    if (this.#now() - startedAt < this.#stableResetMs) {
      return false;
    }
    this.reset();
    return true;
  }

  reset(): void {
    this.#consecutiveFailures = 0;
    this.#failureTimes = [];
    this.#circuitOpenUntil = null;
  }
}
