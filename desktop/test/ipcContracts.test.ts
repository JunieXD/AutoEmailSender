import { describe, expect, it } from "vitest";

import { DESKTOP_IPC_CHANNELS } from "../src/contracts/channels.js";

describe("desktop IPC contracts", () => {
  it("keeps every channel unique in the registry", () => {
    const channels = Object.values(DESKTOP_IPC_CHANNELS);

    expect(new Set(channels).size).toBe(channels.length);
  });
});
