import { type Change as Part, diffWords } from "diff";

/** A piece of a rewritten text. A fact changed, such as a date, a number or a link, is to be checked. */
export type Change = { text: string; kind: "kept" | "removed" | "added"; fact: boolean };

// Chinese has no spaces between words; the segmenter knows where they end.
const words = new Intl.Segmenter("zh", { granularity: "word" });
// What a model most often gets wrong when it changes it (docs/design.md#邮件格式).
const NUMBER = /[0-9０-９]/;
const LINK = /(?:https?:\/\/|mailto:|www\.)[^\s<>]+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+/gi;

/** How `after` differs from `before`, word by word. */
export function changes(before: string, after: string): Change[] {
  const parts = diffWords(before, after, { intlSegmenter: words });
  // A link is checked whole: the part that changed may be a word inside it.
  const facts = new Set([
    ...changedFacts(parts.filter((p) => !p.added)),
    ...changedFacts(parts.filter((p) => !p.removed)),
  ]);
  return parts.map((p) => ({
    text: p.value,
    kind: p.added ? "added" : p.removed ? "removed" : "kept",
    fact: facts.has(p),
  }));
}

/** The changed parts of one side of a diff that hold a number or fall in a link. */
function changedFacts(side: Part[]): Part[] {
  const links = [
    ...side
      .map((p) => p.value)
      .join("")
      .matchAll(LINK),
  ].map((m) => ({
    start: m.index,
    end: m.index + m[0].length,
  }));
  let start = 0;
  return side.filter((p) => {
    const end = start + p.value.length;
    const fact =
      (p.added || p.removed) && (NUMBER.test(p.value) || links.some((link) => link.start < end && start < link.end));
    start = end;
    return fact;
  });
}
