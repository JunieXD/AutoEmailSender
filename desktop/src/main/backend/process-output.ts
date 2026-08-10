import type { ChildProcessWithoutNullStreams } from "node:child_process";

export const PROCESS_STREAM_TAIL_LIMIT_BYTES = 1024 * 1024;

export class BoundedProcessOutputTail {
  readonly #maxBytes: number;
  readonly #chunks: Buffer[] = [];
  #byteLength = 0;

  constructor(maxBytes = PROCESS_STREAM_TAIL_LIMIT_BYTES) {
    if (!Number.isSafeInteger(maxBytes) || maxBytes < 1) {
      throw new Error("Process output tail size must be a positive integer.");
    }
    this.#maxBytes = maxBytes;
  }

  get byteLength(): number {
    return this.#byteLength;
  }

  append(value: Buffer | string): void {
    let chunk = Buffer.isBuffer(value) ? value : Buffer.from(value, "utf8");
    if (chunk.byteLength >= this.#maxBytes) {
      chunk = chunk.subarray(chunk.byteLength - this.#maxBytes);
      this.#chunks.length = 0;
      this.#chunks.push(chunk);
      this.#byteLength = chunk.byteLength;
      return;
    }

    this.#chunks.push(chunk);
    this.#byteLength += chunk.byteLength;
    while (this.#byteLength > this.#maxBytes && this.#chunks.length > 0) {
      const overflow = this.#byteLength - this.#maxBytes;
      const first = this.#chunks[0];
      if (first.byteLength <= overflow) {
        this.#chunks.shift();
        this.#byteLength -= first.byteLength;
        continue;
      }
      this.#chunks[0] = first.subarray(overflow);
      this.#byteLength -= overflow;
    }
  }

  sanitizedText(secrets: readonly string[] = []): string {
    return sanitizeProcessOutput(Buffer.concat(this.#chunks).toString("utf8"), secrets);
  }
}

export type CapturedProcessOutput = {
  stdout: BoundedProcessOutputTail;
  stderr: BoundedProcessOutputTail;
};

export function captureProcessOutput(
  child: Pick<ChildProcessWithoutNullStreams, "stdout" | "stderr">,
): CapturedProcessOutput {
  const output: CapturedProcessOutput = {
    stdout: new BoundedProcessOutputTail(),
    stderr: new BoundedProcessOutputTail(),
  };
  child.stdout.on("data", (chunk: Buffer | string) => output.stdout.append(chunk));
  child.stderr.on("data", (chunk: Buffer | string) => output.stderr.append(chunk));
  return output;
}

export function sanitizeProcessOutput(
  value: string,
  secrets: readonly string[] = [],
): string {
  let sanitized = value;
  for (const secret of [...secrets].filter(Boolean).sort((left, right) => right.length - left.length)) {
    sanitized = sanitized.split(secret).join("[REDACTED]");
  }
  sanitized = sanitized.replace(
    /(https?:\/\/[^\s<>'"]+)/giu,
    (rawUrl) => {
      try {
        const url = new URL(rawUrl);
        url.search = "";
        url.hash = "";
        return url.toString();
      } catch {
        return rawUrl;
      }
    },
  );
  sanitized = sanitized.replace(
    /(Authorization\s*:\s*Bearer\s+)[^\s,;]+/giu,
    "$1[REDACTED]",
  );
  sanitized = sanitized.replace(
    /(Authorization\s*:)(?!\s*Bearer\b)\s*[^\r\n]+/giu,
    "$1 [REDACTED]",
  );
  sanitized = sanitized.replace(
    /(Cookie|Set-Cookie)(\s*:\s*)[^\r\n]+/giu,
    "$1$2[REDACTED]",
  );
  sanitized = sanitized.replace(
    /(\b(?:api[_-]?key|cookie|password|secret|smtpPassword|token)\b["']?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,;]+)/giu,
    "$1[REDACTED]",
  );
  sanitized = sanitized.replace(
    /(\b(?:body(?:[_-]?(?:html|text))?|content|email[_-]?body|generated[_-]?content[_-]?text|payload|request[_-]?body|response[_-]?body)\b["']?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\r\n]*)/giu,
    "$1[REDACTED]",
  );
  return sanitized;
}
