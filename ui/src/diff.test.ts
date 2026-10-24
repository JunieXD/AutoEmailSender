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

test("a date in Chinese is a fact, and a sentence after an address is not part of it", () => {
  expect(facts("十一月三日周二有空", "十一月五日周四有空")).toEqual([
    ["removed", "三日周二"],
    ["added", "五日周四"],
  ]);
  expect(facts("上星期三和明天下午有空", "下星期三和后天下午有空")).toEqual([
    ["removed", "上"],
    ["added", "下"],
    ["removed", "明天"],
    ["added", "后天"],
  ]);
  expect(
    facts("主页https://example.org。我的方向是人工智能。", "主页https://example.org。我的方向是计算机视觉。"),
  ).toEqual([]);
});
