import { type LLMProfileDeletionImpactDTO } from "@/types";

export const isDeletionImpact = (
  value: unknown,
): value is LLMProfileDeletionImpactDTO =>
  typeof value === "object" &&
  value !== null &&
  "revision" in value &&
  typeof value.revision === "string" &&
  "blockers" in value &&
  Array.isArray(value.blockers);
