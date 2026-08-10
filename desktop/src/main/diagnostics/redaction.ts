import path from "node:path";

export const BETA_DIAGNOSTIC_REDACTED = "[REDACTED]";
export const BETA_DIAGNOSTIC_FREE_TEXT_OMITTED = "[FREE_TEXT_OMITTED]";
const MAX_FREE_TEXT_LENGTH = 240;

const TIMELINE_DETAIL_KEYS = new Set([
  "api_available",
  "api_pid",
  "backoff_ms",
  "clock_offset_ms",
  "code",
  "current_version",
  "effective_mode",
  "elapsed_seconds",
  "error_code",
  "marker_category",
  "mode",
  "note",
  "partial_reason",
  "phase",
  "previous_version",
  "process_id",
  "reason",
  "requested_mode",
  "restart_count",
  "runtime_id",
  "signal",
  "sleep_gap_ms",
  "source",
  "state",
  "worker_count",
  "worker_health",
  "worker_pid",
]);

const EMAIL_PATTERN = /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/giu;
const NON_LOOPBACK_IP_PATTERN = /\b(?!(?:127(?:\.\d{1,3}){3}|0\.0\.0\.0|::1)\b)(?:\d{1,3}\.){3}\d{1,3}\b/gu;
const WINDOWS_USER_PATH_PATTERN = /\b[A-Za-z]:\\Users\\[^\\\s]+(?:\\[^\s"'<>]*)?/giu;
const POSIX_HOME_PATH_PATTERN = /\/(?:Users|home)\/[^/\s]+(?:\/[^\s"'<>]*)?/gu;
const CREDENTIAL_PATTERN = /\b(?:api[_-]?key|authorization|cookie|password|secret|smtp[_-]?password|token)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;&]+)/giu;
const BEARER_PATTERN = /\bBearer\s+[A-Za-z0-9._~+/=-]+/giu;
const NAMED_PERSON_PATTERN = /\b(?:name|professor|mentor)\s*[:=]\s*[^\s,;]+|(?:姓名|导师|教授)\s*[:：=]\s*[\p{Script=Han}·]{2,12}/giu;
const URL_PATTERN = /https?:\/\/[^\s"'<>]+/giu;
const OMITTED_NOTE_PATTERN = /^\[FREE_TEXT_OMITTED(?: tags=([a-z_,.-]+))?\]$/u;
const DIAGNOSTIC_NOTE_TAGS: ReadonlyArray<readonly [string, RegExp]> = [
  ["api", /\bapi\b/iu],
  ["background_stall", /卡住|停滞|无响应|\b(?:hang|hung|stall|stuck|unresponsive)\b/iu],
  ["crash", /崩溃|闪退|\bcrash(?:ed)?\b/iu],
  ["crawler", /抓取|爬虫|\bcrawl(?:er|ing)?\b/iu],
  ["database", /数据库|\b(?:database|sqlite)\b/iu],
  ["disk", /磁盘|空间不足|\b(?:disk|enospc|no space left)\b/iu],
  ["email_delivery", /邮件|发送|投递|\b(?:email|smtp|delivery)\b/iu],
  ["mode_switch", /模式|切换|\bmode switch\b/iu],
  ["network", /网络|断网|\b(?:network|offline|connection)\b|https?:\/\//iu],
  ["permission", /权限|只读|\b(?:permission|read-only|eacces|eperm)\b/iu],
  ["resource_usage", /资源|内存|占用|\b(?:cpu|memory|rss|resource)\b/iu],
  ["restart", /重启|\brestart(?:ed|ing)?\b/iu],
  ["sleep_wake", /睡眠|唤醒|休眠|\b(?:sleep|wake|resume)\b/iu],
  ["startup", /启动|初始化|\b(?:start|startup|launch|initializ(?:e|ing))\b/iu],
  ["timeout", /超时|\btime(?:d)?[ -]?out\b/iu],
  ["worker", /后台进程|\bworker\b/iu],
];
const DIAGNOSTIC_NOTE_TAG_NAMES = new Set(DIAGNOSTIC_NOTE_TAGS.map(([tag]) => tag));

export type RedactionContext = {
  homePath?: string;
  userDataPath?: string;
  machineName?: string;
  additionalForbiddenValues?: readonly string[];
};

export function sanitizeTimelineDetails(
  value: Record<string, unknown> | undefined,
  context: RedactionContext = {},
): Record<string, string | number | boolean | null> {
  if (value === undefined) {
    return {};
  }
  const result: Record<string, string | number | boolean | null> = {};
  for (const [key, rawValue] of Object.entries(value)) {
    if (!TIMELINE_DETAIL_KEYS.has(key)) {
      continue;
    }
    if (rawValue === null || typeof rawValue === "boolean") {
      result[key] = rawValue;
      continue;
    }
    if (typeof rawValue === "number" && Number.isFinite(rawValue)) {
      result[key] = rawValue;
      continue;
    }
    if (typeof rawValue !== "string") {
      continue;
    }
    result[key] = key === "note"
      ? sanitizeDiagnosticFreeText(rawValue, context)
      : sanitizeIdentifier(rawValue, context);
  }
  return result;
}

export function sanitizeDiagnosticFreeText(
  value: string,
  context: RedactionContext = {},
): string {
  const normalized = value.normalize("NFC").trim();
  const existing = OMITTED_NOTE_PATTERN.exec(normalized);
  if (existing) {
    const existingTags = existing[1]?.split(",").filter(Boolean) ?? [];
    if (existingTags.every((tag) => DIAGNOSTIC_NOTE_TAG_NAMES.has(tag))) {
      return existingTags.length > 0
        ? `[FREE_TEXT_OMITTED tags=${[...new Set(existingTags)].sort().join(",")}]`
        : BETA_DIAGNOSTIC_FREE_TEXT_OMITTED;
    }
  }
  let sanitized = value.normalize("NFC");
  sanitized = replaceForbiddenValues(sanitized, context);
  sanitized = sanitized.replace(URL_PATTERN, (rawUrl) => sanitizeUrl(rawUrl));
  sanitized = sanitized.replace(EMAIL_PATTERN, "[EMAIL_REDACTED]");
  sanitized = sanitized.replace(WINDOWS_USER_PATH_PATTERN, "[PATH_REDACTED]");
  sanitized = sanitized.replace(POSIX_HOME_PATH_PATTERN, "[PATH_REDACTED]");
  sanitized = sanitized.replace(NON_LOOPBACK_IP_PATTERN, "[IP_REDACTED]");
  sanitized = sanitized.replace(BEARER_PATTERN, BETA_DIAGNOSTIC_REDACTED);
  sanitized = sanitized.replace(CREDENTIAL_PATTERN, BETA_DIAGNOSTIC_REDACTED);
  sanitized = sanitized.replace(NAMED_PERSON_PATTERN, "[PERSON_REDACTED]");
  sanitized = sanitized.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/gu, "");
  const bounded = sanitized.length > MAX_FREE_TEXT_LENGTH
    ? sanitized.slice(0, MAX_FREE_TEXT_LENGTH)
    : sanitized;
  const tags = DIAGNOSTIC_NOTE_TAGS
    .filter(([, pattern]) => pattern.test(bounded))
    .map(([tag]) => tag)
    .sort();
  return tags.length > 0
    ? `[FREE_TEXT_OMITTED tags=${tags.join(",")}]`
    : BETA_DIAGNOSTIC_FREE_TEXT_OMITTED;
}

export function sanitizeDiagnosticLogText(
  value: string,
  context: RedactionContext = {},
): string {
  return value
    .split(/\r?\n/gu)
    .map((line) => sanitizeDiagnosticFreeText(line, context))
    .join("\n");
}

function sanitizeIdentifier(value: string, context: RedactionContext): string {
  const normalized = value.trim();
  const withoutForbiddenValues = replaceForbiddenValues(normalized, context);
  if (
    withoutForbiddenValues !== normalized
    || /^https?:\/\//iu.test(normalized)
    || normalized.startsWith("/")
    || normalized.includes("\\")
    || /\b(?:\d{1,3}\.){3}\d{1,3}\b/u.test(normalized)
  ) {
    return BETA_DIAGNOSTIC_REDACTED;
  }
  if (/^[A-Za-z0-9_.:/+-]{0,160}$/u.test(normalized)) {
    return normalized;
  }
  return BETA_DIAGNOSTIC_REDACTED;
}

function replaceForbiddenValues(value: string, context: RedactionContext): string {
  const candidates = [
    context.homePath,
    context.userDataPath,
    context.machineName,
    ...(context.additionalForbiddenValues ?? []),
  ]
    .filter((candidate): candidate is string => Boolean(candidate?.trim()))
    .sort((left, right) => right.length - left.length);
  let sanitized = value;
  for (const candidate of candidates) {
    const variants = new Set([candidate, path.normalize(candidate)]);
    for (const variant of variants) {
      sanitized = sanitized.split(variant).join(BETA_DIAGNOSTIC_REDACTED);
    }
  }
  return sanitized;
}

function sanitizeUrl(rawValue: string): string {
  try {
    const parsed = new URL(rawValue);
    const isLoopback = parsed.hostname === "127.0.0.1"
      || parsed.hostname === "localhost"
      || parsed.hostname === "[::1]";
    if (!isLoopback) {
      return "[URL_REDACTED]";
    }
    parsed.search = "";
    parsed.hash = "";
    return parsed.toString();
  } catch {
    return "[URL_REDACTED]";
  }
}
