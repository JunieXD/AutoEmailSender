import type { CrawlCandidateDTO, CrawlJobEventDTO } from "@/types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function getCrawlEventRawPayload(
  event: CrawlJobEventDTO,
): Record<string, unknown> | null {
  if (!isRecord(event.raw)) {
    return null;
  }

  let current: Record<string, unknown> = event.raw;
  for (let depth = 0; depth < 8; depth += 1) {
    if (
      current.status === "failed" &&
      typeof current.error_message === "string" &&
      current.error_message.trim().length > 0
    ) {
      return current;
    }

    if (!isRecord(current.raw)) {
      return current;
    }

    current = current.raw;
  }

  return current;
}

function getEventTime(event: CrawlJobEventDTO): number {
  if (!event.created_at) {
    return 0;
  }
  const value = Date.parse(event.created_at);
  return Number.isNaN(value) ? 0 : value;
}

export function getCandidateEnrichmentFailureMessage(
  candidate: CrawlCandidateDTO,
  events: CrawlJobEventDTO[],
): string | null {
  const candidateEvents = events
    .map((event, index) => ({ event, index, raw: getCrawlEventRawPayload(event) }))
    .filter(({ event, raw }) => {
      if (event.event_type !== "enrichment") {
        return false;
      }
      if (!raw) {
        return false;
      }
      return raw.candidate_id === candidate.id;
    });

  if (candidateEvents.length === 0) {
    return null;
  }

  const latest = candidateEvents.reduce((current, item) => {
    const currentTime = getEventTime(current.event);
    const itemTime = getEventTime(item.event);
    if (itemTime !== currentTime) {
      return itemTime > currentTime ? item : current;
    }
    return item.index > current.index ? item : current;
  });

  const rawStatus = latest.raw?.status;
  const rawErrorMessage = latest.raw?.error_message;
  if (
    rawStatus !== "failed" ||
    typeof rawErrorMessage !== "string" ||
    rawErrorMessage.trim().length === 0
  ) {
    return null;
  }
  return rawErrorMessage;
}

export function getCrawlEventFailureReason(event: CrawlJobEventDTO): string | null {
  if (event.event_type !== "enrichment") {
    return null;
  }
  const raw = getCrawlEventRawPayload(event);
  if (!raw) {
    return null;
  }
  const rawStatus = raw.status;
  const rawErrorMessage = raw.error_message;
  if (
    rawStatus !== "failed" ||
    typeof rawErrorMessage !== "string" ||
    rawErrorMessage.trim().length === 0
  ) {
    return null;
  }
  return rawErrorMessage;
}

export function getCrawlEventStableKey(event: CrawlJobEventDTO): string {
  return [
    event.id,
    event.event_type,
    event.created_at ?? "",
    event.message,
  ].join("|");
}

export function isCrawlEnrichmentCompletionEvent(
  event: CrawlJobEventDTO,
): boolean {
  return (
    event.event_type === "enrichment" &&
    event.message.trim().startsWith("候选导师详情补全完成：")
  );
}
