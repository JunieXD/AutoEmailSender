export type DiagnosticLevel = "debug" | "info" | "warn" | "error";
export type DiagnosticCategory = "user_action" | "api" | "frontend_error" | "system";

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export interface DiagnosticEvent {
  id: string;
  sessionId: string;
  timestamp: string;
  level: DiagnosticLevel;
  category: DiagnosticCategory;
  eventName: string;
  message?: string;
  data?: JsonValue;
}

export interface DiagnosticEventInput {
  level: DiagnosticLevel;
  category: DiagnosticCategory;
  eventName: string;
  message?: string;
  data?: unknown;
}

interface DiagnosticExport {
  exportedAt: string;
  sessionId: string;
  events: DiagnosticEvent[];
}

const diagnosticsStorageKey = "auto-email-diagnostics:v1";
const diagnosticsSessionStorageKey = "auto-email-diagnostics-session:v1";
const maxStoredEvents = 500;
const maxDiagnosticStringLength = 300;
const redactedValue = "[Redacted]";
const unserializableValue = "[Unserializable]";
const sensitiveKeyFragments = [
  "token",
  "secret",
  "password",
  "authorization",
  "cookie",
  "apikey",
];
const payloadKeys = new Set(["body", "requestbody", "responsebody", "rawbody", "payload"]);

let memoryEvents: DiagnosticEvent[] = [];
let memorySessionId: string | undefined;

export function recordDiagnosticEvent(input: DiagnosticEventInput): DiagnosticEvent {
  const event: DiagnosticEvent = {
    id: createId(),
    sessionId: getDiagnosticSessionId(),
    timestamp: new Date().toISOString(),
    level: input.level,
    category: input.category,
    eventName: input.eventName,
  };

  if (input.message !== undefined) {
    event.message = sanitizeDiagnosticString(input.message);
  }

  if (Object.prototype.hasOwnProperty.call(input, "data")) {
    event.data = safeToJsonValue(input.data, new WeakSet<object>());
  }

  try {
    const events = [...readEvents(), event].slice(-maxStoredEvents);
    writeEvents(events);
  } catch {
    memoryEvents = [...memoryEvents, event].slice(-maxStoredEvents);
  }

  return event;
}

export function getDiagnosticEvents(): DiagnosticEvent[] {
  try {
    return readEvents();
  } catch {
    return [...memoryEvents];
  }
}

export function clearDiagnosticEvents(): void {
  memoryEvents = [];

  try {
    getStorage("local")?.removeItem(diagnosticsStorageKey);
  } catch {
    // localStorage failures must not leak into app flows.
  }
}

export function exportDiagnosticEvents(): string {
  const payload: DiagnosticExport = {
    exportedAt: new Date().toISOString(),
    sessionId: getDiagnosticSessionId(),
    events: getDiagnosticEvents(),
  };

  try {
    return JSON.stringify(payload, null, 2);
  } catch {
    return JSON.stringify({
      exportedAt: payload.exportedAt,
      sessionId: payload.sessionId,
      events: [],
    });
  }
}

export function getDiagnosticSessionId(): string {
  if (memorySessionId) {
    return memorySessionId;
  }

  try {
    const storage = getStorage("session");
    const storedSessionId = storage?.getItem(diagnosticsSessionStorageKey);
    if (storedSessionId) {
      memorySessionId = storedSessionId;
      return storedSessionId;
    }

    memorySessionId = createId();
    storage?.setItem(diagnosticsSessionStorageKey, memorySessionId);
    return memorySessionId;
  } catch {
    memorySessionId = createId();
    return memorySessionId;
  }
}

function readEvents(): DiagnosticEvent[] {
  const storage = getStorage("local");
  if (!storage) {
    return [...memoryEvents];
  }

  let rawEvents: string | null;
  try {
    rawEvents = storage.getItem(diagnosticsStorageKey);
  } catch {
    return [...memoryEvents];
  }

  if (!rawEvents) {
    return [];
  }

  try {
    const parsedEvents = JSON.parse(rawEvents);
    return Array.isArray(parsedEvents) ? parsedEvents : [];
  } catch {
    return [];
  }
}

function writeEvents(events: DiagnosticEvent[]): void {
  const storage = getStorage("local");
  if (!storage) {
    memoryEvents = [...events];
    return;
  }

  storage.setItem(diagnosticsStorageKey, JSON.stringify(events));
  memoryEvents = [...events];
}

function getStorage(type: "local" | "session"): Storage | undefined {
  if (typeof window === "undefined") {
    return undefined;
  }

  return type === "local" ? window.localStorage : window.sessionStorage;
}

function createId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }

  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function safeToJsonValue(value: unknown, seen: WeakSet<object>, key?: string): JsonValue {
  try {
    if (key && shouldRedactKey(key)) {
      return redactedValue;
    }

    return toJsonValue(value, seen, key);
  } catch {
    return unserializableValue;
  }
}

function toJsonValue(value: unknown, seen: WeakSet<object>, key?: string): JsonValue {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return typeof value === "string" ? sanitizeStringValue(value, key) : value;
  }

  if (typeof value === "number") {
    return Number.isFinite(value) ? value : String(value);
  }

  if (typeof value === "bigint" || typeof value === "symbol") {
    return String(value);
  }

  if (typeof value === "undefined") {
    return "[Undefined]";
  }

  if (typeof value === "function") {
    return "[Function]";
  }

  if (value instanceof Error) {
    return {
      name: safeToJsonValue(value.name, seen),
      message: safeToJsonValue(value.message, seen),
    };
  }

  if (typeof Response !== "undefined" && value instanceof Response) {
    return {
      ok: value.ok,
      redirected: value.redirected,
      status: value.status,
      statusText: value.statusText,
      type: value.type,
      url: stripUrlQueryAndHash(value.url),
    };
  }

  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) {
      return "[Invalid Date]";
    }

    return value.toISOString();
  }

  if (Array.isArray(value)) {
    if (seen.has(value)) {
      return "[Circular]";
    }

    seen.add(value);
    const serializedItems = value.map((item) => safeToJsonValue(item, seen, key));
    seen.delete(value);
    return serializedItems;
  }

  if (typeof value === "object") {
    if (seen.has(value)) {
      return "[Circular]";
    }

    seen.add(value);
    const serializedObject: { [key: string]: JsonValue } = {};
    for (const key of Object.keys(value)) {
      serializedObject[key] = safeToJsonValue(readObjectValue(value, key), seen, key);
    }
    seen.delete(value);
    return serializedObject;
  }

  return String(value);
}

function readObjectValue(value: object, key: string): unknown {
  try {
    return (value as Record<string, unknown>)[key];
  } catch {
    return unserializableValue;
  }
}

function shouldRedactKey(key: string): boolean {
  const normalizedKey = key.toLowerCase().replace(/[^a-z0-9]/g, "");
  return (
    payloadKeys.has(normalizedKey) ||
    sensitiveKeyFragments.some((fragment) => normalizedKey.includes(fragment))
  );
}

function sanitizeStringValue(value: string, key?: string): string {
  const sanitizedValue = sanitizeDiagnosticString(value);

  if ((key && key.toLowerCase().includes("url")) || looksLikeUrl(sanitizedValue)) {
    return stripUrlQueryAndHash(sanitizedValue);
  }

  return sanitizedValue;
}

function looksLikeUrl(value: string): boolean {
  return /^https?:\/\//i.test(value);
}

function stripUrlQueryAndHash(value: string): string {
  try {
    const url = new URL(value);
    url.search = "";
    url.hash = "";
    return url.toString();
  } catch {
    return value;
  }
}

function sanitizeDiagnosticString(value: string): string {
  try {
    const withoutSensitiveUrls = value.replace(/https?:\/\/[^\s"'<>]+/gi, (url) => stripUrlQueryAndHash(url));
    const withoutJsonSensitiveValues = withoutSensitiveUrls.replace(
      /(["'])(token|access[_-]?token|refresh[_-]?token|api[_-]?key|apikey|password|secret|authorization|cookie|smtp[_-]?password|smtppassword|client[_-]?secret|session[_-]?cookie|authToken)\1\s*:\s*(["'])(?:\\.|(?!\3).)*\3/gi,
      (_match, quote: string, key: string) => `${quote}${key}${quote}:${quote}${redactedValue}${quote}`,
    );
    const withoutAuthHeaders = withoutJsonSensitiveValues.replace(
      /\bauthorization\s*[:=]\s*Bearer\s+[^\s,;&]+/gi,
      redactedValue,
    );
    const withoutSensitiveKeyValues = withoutAuthHeaders.replace(
      /\b(?:token|access[_-]?token|refresh[_-]?token|api[_-]?key|apikey|password|secret|authorization|cookie|smtp[_-]?password|smtppassword|client[_-]?secret|session[_-]?cookie|authToken)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;&]+)/gi,
      redactedValue,
    );

    return withoutSensitiveKeyValues.length > maxDiagnosticStringLength
      ? `${withoutSensitiveKeyValues.slice(0, maxDiagnosticStringLength)}…`
      : withoutSensitiveKeyValues;
  } catch {
    return unserializableValue;
  }
}
