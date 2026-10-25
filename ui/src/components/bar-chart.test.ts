import { describe, expect, it } from "vitest";
import { niceTicks } from "./bar-chart";

describe("niceTicks", () => {
  it("covers the largest value with a few round steps", () => {
    expect(niceTicks(0)).toEqual([0]);
    expect(niceTicks(1)).toEqual([0, 1]);
    expect(niceTicks(3)).toEqual([0, 1, 2, 3]);
    expect(niceTicks(7)).toEqual([0, 2, 4, 6, 8]);
    expect(niceTicks(13)).toEqual([0, 5, 10, 15]);
    expect(niceTicks(100)).toEqual([0, 50, 100]);
    expect(niceTicks(240)).toEqual([0, 100, 200, 300]);
  });
});
