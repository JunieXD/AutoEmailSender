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

export function reconcileMatches(preview: Preview, choices: Decisions, previous: Preview): Decisions["matches"] {
  return preview.sheet === previous.sheet
    ? choices.matches.filter((choice) => {
        const before = previous.maybeSame.find((m) => m.row === choice.row);
        return (
          before !== undefined &&
          preview.maybeSame.some(
            (m) =>
              m.row === choice.row &&
              m.candidates.length === before.candidates.length &&
              m.candidates.every((c) => before.candidates.some((b) => b.id === c.id)) &&
              (choice.id === null || m.candidates.some((c) => c.id === choice.id)) &&
              m.rows.length === before.rows.length &&
              m.rows.every((r, n) => r === before.rows[n]),
          )
        );
      })
    : [];
}

export function reconcileChoices(preview: Preview, choices: Decisions, previous: Preview): Decisions {
  return {
    take: preview.differences.filter((d) => choices.take.some((choice) => sameDifference(d, choice))),
    matches: reconcileMatches(preview, choices, previous),
    restore:
      preview.sheet === previous.sheet ? choices.restore.filter((id) => preview.trashed.some((m) => m.id === id)) : [],
  };
}
