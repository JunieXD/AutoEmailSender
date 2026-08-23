import { recordDiagnosticEvent } from "@/lib/diagnostics";
import type { DesktopBackendStatus } from "@/types/desktop";

let desktopBackendBaseUrlOverride: string | null = null;

export class ApiError extends Error {
  status: number;
  code: string | null;
  details: unknown;
  payload: unknown;

  constructor(status: number, message: string, payload: unknown = null) {
    super(message);
    this.status = status;
    const metadata = getApiErrorMetadata(payload);
    this.code = metadata.code;
    this.details = metadata.details;
    this.payload = payload;
  }
}

export const buildApiPath = (
  path: string,
  params?: Record<string, string | number | null | undefined>,
) => {
  const baseUrl = getDesktopBackendBaseUrl();
  const url = new URL(path, baseUrl ?? window.location.origin);
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value === null || value === undefined || value === "") {
      return;
    }
    url.searchParams.set(key, String(value));
  });
  return baseUrl ? url.toString() : `${url.pathname}${url.search}`;
};

export const updateDesktopBackendBaseUrl = (baseUrl: string | null | undefined): void => {
  const normalized = baseUrl?.trim().replace(/\/+$/, "");
  desktopBackendBaseUrlOverride = normalized || null;
};

type ApiSuccessParser<T> = (response: Response) => Promise<T>;

export const apiFetch = <T>(
  path: string,
  options?: RequestInit,
  params?: Record<string, string | number | null | undefined>,
): Promise<T> =>
  executeApiRequest(
    path,
    options,
    params,
    async (response) => (await readResponseData(response)) as T,
  );

export const apiFetchBlob = (
  path: string,
  options?: RequestInit,
  params?: Record<string, string | number | null | undefined>,
): Promise<Blob> => executeApiRequest(path, options, params, (response) => response.blob());

async function executeApiRequest<T>(
  path: string,
  options: RequestInit | undefined,
  params: Record<string, string | number | null | undefined> | undefined,
  parseSuccess: ApiSuccessParser<T>,
): Promise<T> {
  const startedAt = now();
  const method = (options?.method ?? "GET").toUpperCase();
  const apiPath = await buildApiPathForFetch(path, params);
  const diagnosticData = {
    method,
    path: stripQueryAndHash(apiPath),
  };
  let lastError: unknown;

  try {
    return await executeApiFetchOnce(apiPath, options, startedAt, method, parseSuccess);
  } catch (error) {
    lastError = error;
    if (shouldRetryDesktopNetworkError(error)) {
      updateDesktopBackendBaseUrl(null);
      try {
        await waitForDesktopBackendBaseUrl();
        return await executeApiFetchOnce(
          await buildApiPathForFetch(path, params),
          options,
          startedAt,
          method,
          parseSuccess,
        );
      } catch (retryError) {
        lastError = retryError;
      }
    }
    if (!(lastError instanceof ApiError)) {
      recordApiDiagnosticEvent({
        level: "error",
        eventName: "api.request_errored",
        data: {
          ...diagnosticData,
          durationMs: elapsedMs(startedAt),
          errorType: getThrownErrorType(lastError),
          error: sanitizeDiagnosticMessage(getThrownErrorMessage(lastError)),
        },
      });
    }
    throw lastError;
  }
}

async function executeApiFetchOnce<T>(
  apiPath: string,
  options: RequestInit | undefined,
  startedAt: number,
  method: string,
  parseSuccess: ApiSuccessParser<T>,
): Promise<T> {
  const headers = new Headers(options?.headers);
  if (!(options?.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const desktopAccessToken = window.autoEmailSender?.getBackendAccessToken?.()?.trim();
  if (desktopAccessToken && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${desktopAccessToken}`);
  }
  const response = await fetch(apiPath, {
    ...options,
    headers,
  });

  if (response.status === 204) {
    recordApiDiagnosticEvent({
      level: "info",
      eventName: "api.request_succeeded",
      data: {
        method,
        path: stripQueryAndHash(apiPath),
        status: response.status,
        durationMs: elapsedMs(startedAt),
      },
    });
    return undefined as T;
  }

  if (!response.ok) {
    const data = await readResponseData(response);
    const message = getApiErrorMessage(data);
    recordApiDiagnosticEvent({
      level: "error",
      eventName: "api.request_failed",
      data: {
        method,
        path: stripQueryAndHash(apiPath),
        status: response.status,
        durationMs: elapsedMs(startedAt),
        message: sanitizeDiagnosticMessage(message),
      },
    });
    throw new ApiError(response.status, message, data);
  }

  const data = await parseSuccess(response);
  recordApiDiagnosticEvent({
    level: "info",
    eventName: "api.request_succeeded",
    data: {
      method,
      path: stripQueryAndHash(apiPath),
      status: response.status,
      durationMs: elapsedMs(startedAt),
    },
  });
  return data;
}

async function readResponseData(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function getApiErrorMessage(data: unknown): string {
  if (typeof data === "object" && data !== null && "detail" in data) {
    const detailMessage = formatDetailMessage(data.detail);
    if (detailMessage) {
      return detailMessage;
    }
  }

  if (
    typeof data === "object" &&
    data !== null &&
    "error" in data &&
    typeof data.error === "object" &&
    data.error !== null &&
    "message" in data.error &&
    typeof data.error.message === "string" &&
    data.error.message.trim()
  ) {
    return data.error.message;
  }

  if (
    typeof data === "object" &&
    data !== null &&
    "message" in data &&
    typeof data.message === "string" &&
    data.message.trim()
  ) {
    return data.message;
  }

  if (typeof data === "string" && data.trim()) {
    return data;
  }

  return "\u8BF7\u6C42\u5931\u8D25";
}

function formatDetailMessage(detail: unknown): string | undefined {
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }

  if (
    typeof detail === "object" &&
    detail !== null &&
    "message" in detail &&
    typeof detail.message === "string" &&
    detail.message.trim()
  ) {
    return detail.message;
  }

  if (!Array.isArray(detail)) {
    return undefined;
  }

  const messages = detail
    .map((item) => {
      if (typeof item === "string") {
        return item.trim();
      }
      if (typeof item !== "object" || item === null || !("msg" in item) || typeof item.msg !== "string") {
        return "";
      }

      const normalizedMessage = normalizeValidationMessage(item.msg);
      if (normalizedMessage !== item.msg) {
        return normalizedMessage;
      }

      const location =
        "loc" in item && Array.isArray(item.loc)
          ? item.loc.filter(isLocationPart).join(".")
          : "";
      return location ? `${location}: ${normalizedMessage}` : normalizedMessage;
    })
    .filter(Boolean);

  return messages.length > 0 ? messages.join("\uFF1B") : undefined;
}

function getApiErrorMetadata(data: unknown): {
  code: string | null;
  details: unknown;
} {
  if (typeof data !== "object" || data === null) {
    return { code: null, details: null };
  }

  const envelope =
    "detail" in data && typeof data.detail === "object" && data.detail !== null
      ? data.detail
      : "error" in data && typeof data.error === "object" && data.error !== null
        ? data.error
        : data;
  const code =
    "code" in envelope && typeof envelope.code === "string"
      ? envelope.code
      : null;
  const details =
    "details" in envelope && envelope.details !== undefined
      ? envelope.details
      : envelope;
  return { code, details };
}

function normalizeValidationMessage(message: string): string {
  return message.replace(/^Value error,\s*/i, "").trim();
}

function isLocationPart(part: unknown): part is string | number {
  return typeof part === "string" || typeof part === "number";
}

function getThrownErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }

  return String(error);
}

function getThrownErrorType(error: unknown): string {
  if (error instanceof Error && error.name) {
    return error.name;
  }

  return typeof error;
}

function shouldRetryDesktopNetworkError(error: unknown): boolean {
  if (!window.autoEmailSender) {
    return false;
  }

  const message = getThrownErrorMessage(error).toLowerCase();
  return (
    error instanceof TypeError &&
    (message.includes("failed to fetch") ||
      message.includes("networkerror") ||
      message.includes("load failed"))
  );
}

function sanitizeDiagnosticMessage(message: string): string {
  try {
    const withoutSensitiveUrls = message.replace(/https?:\/\/[^\s"'<>]+/gi, (value) =>
      stripUrlQueryAndHash(value),
    );
    const withoutAuthHeaders = withoutSensitiveUrls.replace(
      /\bauthorization\s*[:=]\s*Bearer\s+[^\s,;&]+/gi,
      "[Redacted]",
    );
    const withoutSensitiveKeyValues = withoutAuthHeaders.replace(
      /\b(?:token|api[_-]?key|password|secret|authorization|cookie|smtpPassword)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;&]+)/gi,
      "[Redacted]",
    );

    return withoutSensitiveKeyValues.length > 300
      ? `${withoutSensitiveKeyValues.slice(0, 300)}…`
      : withoutSensitiveKeyValues;
  } catch {
    return "[Unserializable]";
  }
}

function recordApiDiagnosticEvent(input: {
  level: "info" | "error";
  eventName: "api.request_succeeded" | "api.request_failed" | "api.request_errored";
  data: Record<string, string | number>;
}): void {
  try {
    recordDiagnosticEvent({
      level: input.level,
      category: "api",
      eventName: input.eventName,
      data: input.data,
    });
  } catch {
    // Diagnostic failures should never change API behavior.
  }
}

function elapsedMs(startedAt: number): number {
  return Math.max(0, Math.round(now() - startedAt));
}

function now(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function stripQueryAndHash(path: string): string {
  const url = new URL(path, window.location.origin);
  return url.pathname;
}

function stripUrlQueryAndHash(value: string): string {
  const url = new URL(value);
  url.search = "";
  url.hash = "";
  return url.toString();
}

function getDesktopBackendBaseUrl(): string | null {
  const desktopApi = window.autoEmailSender;
  const baseUrl =
    desktopBackendBaseUrlOverride ??
    desktopApi?.getBackendBaseUrl?.()?.trim() ??
    desktopApi?.backendBaseUrl?.trim();
  return baseUrl ? baseUrl.replace(/\/+$/, "") : null;
}

function getDesktopBackendStartupErrorMessage(status: Extract<DesktopBackendStatus, { state: "error" }>): string {
  if (status.state === "error" && status.databaseError?.code === "DATABASE_REQUIRES_NEWER_APP") {
    return [
      `当前数据需要 AutoEmailSender ${status.databaseError.minimumSupportedAppVersion} 或更高版本。`,
      "请升级到新版继续使用。若必须回退旧版，请从升级前备份恢复数据库。",
      `备份位置：${status.databaseError.backupDirectory}`,
    ].join("\n");
  }

  if (status.message?.includes("系统准备时间过长")) {
    return "本地数据准备时间较长。请重启应用后再试；如果问题仍然存在，请导出诊断日志反馈。";
  }

  return "系统准备失败。请重启应用后再试；如果问题仍然存在，请导出诊断日志反馈。";
}

async function buildApiPathForFetch(
  path: string,
  params?: Record<string, string | number | null | undefined>,
): Promise<string> {
  if (window.autoEmailSender) {
    await waitForDesktopBackendBaseUrl();
  }
  return buildApiPath(path, params);
}

async function waitForDesktopBackendBaseUrl(): Promise<string | null> {
  const currentBaseUrl = getDesktopBackendBaseUrl();
  if (currentBaseUrl || !window.autoEmailSender) {
    return currentBaseUrl;
  }

  const subscribe = window.autoEmailSender.onBackendStatus;
  if (!subscribe) {
    return null;
  }

  return new Promise((resolve, reject) => {
    let unsubscribe: () => void = () => undefined;
    const timeout = window.setTimeout(() => {
      unsubscribe();
      reject(new Error("系统正在准备本地数据。请保持应用打开，完成后再继续操作。"));
    }, 10 * 60_000);
    unsubscribe = subscribe((status) => {
      if (status.state === "ready") {
        window.clearTimeout(timeout);
        updateDesktopBackendBaseUrl(status.baseUrl);
        unsubscribe();
        resolve(status.baseUrl);
      }
      if (status.state === "error") {
        window.clearTimeout(timeout);
        unsubscribe();
        reject(new Error(getDesktopBackendStartupErrorMessage(status)));
      }
    });
  });
}
