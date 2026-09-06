import { type IdentityDeletionImpactDTO } from "@/types";

export const isIdentityDeletionImpact = (
  value: unknown,
): value is IdentityDeletionImpactDTO =>
  typeof value === "object" &&
  value !== null &&
  "identity_id" in value &&
  "revision" in value &&
  typeof value.revision === "string" &&
  "blockers" in value &&
  Array.isArray(value.blockers);
