import { spawn } from "node:child_process";
import { once } from "node:events";
import { describe, expect, it } from "vitest";
import {
  BoundedProcessOutputTail,
  PROCESS_STREAM_TAIL_LIMIT_BYTES,
  captureProcessOutput,
  sanitizeProcessOutput,
} from "../src/main/backend/process-output.js";

describe("bounded child process output", () => {
  it("retains only the configured byte tail", () => {
    const tail = new BoundedProcessOutputTail(8);
    tail.append("12345");
    tail.append("67890");

    expect(tail.byteLength).toBe(8);
    expect(tail.sanitizedText()).toBe("34567890");
  });

  it("redacts access tokens, credentials, URLs, and message bodies", () => {
    const sanitized = sanitizeProcessOutput(
      "token=secret-token password=secret-password body=private message\n"
      + "Authorization: Bearer bearer-secret https://example.test/path?q=url-secret#part\n"
      + "exact-agent-token",
      ["exact-agent-token"],
    );

    expect(sanitized).toContain("token=[REDACTED]");
    expect(sanitized).toContain("password=[REDACTED]");
    expect(sanitized).toContain("body=[REDACTED]");
    expect(sanitized).toContain("Authorization: Bearer [REDACTED]");
    expect(sanitized).toContain("https://example.test/path");
    expect(sanitized).not.toContain("secret-token");
    expect(sanitized).not.toContain("secret-password");
    expect(sanitized).not.toContain("private message");
    expect(sanitized).not.toContain("bearer-secret");
    expect(sanitized).not.toContain("url-secret");
    expect(sanitized).not.toContain("exact-agent-token");
  });

  it("drains 100 MiB from stdout and stderr for each of two children", async () => {
    const script = String.raw`
const chunk = Buffer.alloc(1024 * 1024, "x");
const write = (stream) => new Promise((resolve) => {
  if (stream.write(chunk)) resolve();
  else stream.once("drain", resolve);
});
(async () => {
  for (let index = 0; index < 100; index += 1) {
    await Promise.all([write(process.stdout), write(process.stderr)]);
  }
  process.stdout.write("STDOUT_DONE");
  process.stderr.write("STDERR_DONE");
})().catch((error) => { console.error(error); process.exitCode = 1; });
`;
    const children = [0, 1].map(() => spawn(process.execPath, ["-e", script], {
      stdio: ["pipe", "pipe", "pipe"],
    }));
    const outputs = children.map((child) => captureProcessOutput(child));

    const exits = await Promise.all(children.map(async (child) => {
      const [code] = await once(child, "exit") as [number | null, NodeJS.Signals | null];
      return code;
    }));

    expect(exits).toEqual([0, 0]);
    for (const output of outputs) {
      expect(output.stdout.byteLength).toBe(PROCESS_STREAM_TAIL_LIMIT_BYTES);
      expect(output.stderr.byteLength).toBe(PROCESS_STREAM_TAIL_LIMIT_BYTES);
      expect(output.stdout.sanitizedText()).toMatch(/x+STDOUT_DONE$/);
      expect(output.stderr.sanitizedText()).toMatch(/x+STDERR_DONE$/);
    }
  }, 30_000);
});
