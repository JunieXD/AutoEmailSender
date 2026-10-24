import { expect, test } from "vitest";
import { changes } from "./diff";

test("an added date is a fact to check, and Chinese is compared word by word", () => {
  expect(changes("下周二下午或者周三上午我都有空。", "下周二（10月28日）下午或周三上午我都有空。")).toEqual([
    { text: "下周二", kind: "kept", fact: false },
    { text: "（10月28日）", kind: "added", fact: true },
    { text: "下午", kind: "kept", fact: false },
    { text: "或者", kind: "removed", fact: false },
    { text: "或", kind: "added", fact: false },
    { text: "周三上午我都有空。", kind: "kept", fact: false },
  ]);
});

/** The changes that are facts to check, as [kind, text]. */
const facts = (before: string, after: string) =>
  changes(before, after)
    .filter((c) => c.fact)
    .map((c) => [c.kind, c.text]);

test("a link changed anywhere and a number taken out are facts to check", () => {
  expect(facts("主页 <https://old.example.org/cv>", "主页 <https://new.example.org/cv>")).toEqual([
    ["removed", "old.example.org"],
    ["added", "new.example.org"],
  ]);
  expect(facts("主页 <https://example.org/old/cv>", "主页 <https://example.org/cv>")).toEqual([["removed", "old/"]]);
  expect(facts("成绩 90 分", "成绩优秀")).toEqual([["removed", " 90 分"]]);
});
