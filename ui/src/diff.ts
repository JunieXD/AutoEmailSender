import { diffWords } from "diff";

/** A piece of a rewritten text. An added fact, such as a date, a number or a link, is to be checked. */
export type Change = { text: string; kind: "kept" | "removed" | "added" | "fact" };

// Chinese has no spaces between words; the segmenter knows where they end.
const words = new Intl.Segmenter("zh", { granularity: "word" });
// What a model most often gets wrong when it adds it (docs/design.md#邮件格式).
const FACT = /[0-9０-９]|https?:|www\.|@/i;

/** How `after` differs from `before`, word by word. */
export function changes(before: string, after: string): Change[] {
  return diffWords(before, after, { intlSegmenter: words }).map((c) => ({
    text: c.value,
    kind: c.removed ? "removed" : !c.added ? "kept" : FACT.test(c.value) ? "fact" : "added",
  }));
}
