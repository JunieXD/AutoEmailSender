import type { Decisions, Difference, Preview } from "@/api/types";

export function sameDifference(a: Difference, b: Difference): boolean {
  return (
    a.id === b.id &&
    a.field === b.field &&
    a.local === b.local &&
    a.incoming === b.incoming &&
    (a.affiliation === null
      ? b.affiliation === null
      : b.affiliation !== null &&
        a.affiliation.university === b.affiliation.university &&
        a.affiliation.school === b.affiliation.school &&
        a.affiliation.department === b.affiliation.department &&
        a.affiliation.title === b.affiliation.title)
  );
}

export function reconcileChoices(preview: Preview, choices: Decisions): Decisions {
  return {
    take: preview.differences.filter((d) => choices.take.some((choice) => sameDifference(d, choice))),
    separate: choices.separate.filter((row) => preview.maybeSame.some((m) => m.row === row)),
    restore: choices.restore.filter((id) => preview.trashed.some((m) => m.id === id)),
  };
}
