import { type Change as Part, diffWords } from "diff";

/** A piece of a rewritten text. A fact changed, such as a date, a number or a link, is to be checked. */
export type Change = { text: string; kind: "kept" | "removed" | "added"; fact: boolean };

// Chinese has no spaces between words; the segmenter knows where they end.
const words = new Intl.Segmenter("zh", { granularity: "word" });
// What a model most often gets wrong when it changes it (docs/design.md#邮件格式): numbers, and
// links and dates in Chinese, which are checked whole as the part that changed may be inside one.
const NUMBER = /[0-9０-９]/;
const WHOLE = new RegExp(
  [
    // A link ends where the characters an address may have do, such as at Chinese punctuation.
    String.raw`(?:https?://|mailto:|www\.)[\w\-.~:/?#[\]@!$&'()*+,;=%]+`,
    String.raw`[\w.+-]+@[\w-]+(?:\.[\w-]+)+`,
    "[〇零一二三四五六七八九十两]+[年月日号]",
    "(?:[上下本这]个?)?(?:周|星期|礼拜)[一二三四五六日天]",
    "[上下本这]个?(?:周|星期|礼拜|月)",
    "[今明后昨前]天",
  ].join("|"),
  "gi",
);

/** How `after` differs from `before`, word by word. */
export function changes(before: string, after: string): Change[] {
  const parts = diffWords(before, after, { intlSegmenter: words });
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

/** The changed parts of one side of a diff that hold a number or fall in a link or a date. */
function changedFacts(side: Part[]): Part[] {
  const text = side.map((p) => p.value).join("");
  const wholes = [...text.matchAll(WHOLE)].map((m) => ({ start: m.index, end: m.index + m[0].length }));
  let start = 0;
  return side.filter((p) => {
    const end = start + p.value.length;
    const fact = (p.added || p.removed) && (NUMBER.test(p.value) || wholes.some((w) => w.start < end && start < w.end));
    start = end;
    return fact;
  });
}
