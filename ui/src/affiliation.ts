import type { Affiliation } from "@/api/types";

const PARTS = ["university", "school", "department"] as const;
const text = (value: string) => value.trim().replace(/\s+/g, " ");

/** Predicts the number shown before a merge, including compatible units with blank parts. */
export function mergedAffiliationCount(kept: Affiliation[], other: Affiliation[]): number {
  const places = kept.map((a) => ({ ...a }));
  for (const incoming of other) {
    const exact = places.find((a) =>
      PARTS.every(
        (p) => a[p] === incoming[p] || (a[p] !== null && incoming[p] !== null && text(a[p]) === text(incoming[p])),
      ),
    );
    const local =
      exact ??
      places.find((a) => PARTS.every((p) => a[p] === null || incoming[p] === null || text(a[p]) === text(incoming[p])));
    if (local) {
      for (const part of PARTS) local[part] ??= incoming[part];
    } else places.push({ ...incoming });
  }
  return places.length;
}
