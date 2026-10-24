import { expect, test } from "vitest";
import { changes } from "./diff";

test("an added date or link is a fact to check, and Chinese is compared word by word", () => {
  const before = "下周二下午或者周三上午我都有空。";
  const after = "下周二（10月28日）下午或周三上午我都有空，主页见 https://example.com。";
  expect(changes(before, after)).toEqual([
    { text: "下周二", kind: "kept" },
    { text: "（10月28日）", kind: "fact" },
    { text: "下午", kind: "kept" },
    { text: "或者", kind: "removed" },
    { text: "或", kind: "added" },
    { text: "周三上午我都有空", kind: "kept" },
    { text: "，主页见 https://example.com", kind: "fact" },
    { text: "。", kind: "kept" },
  ]);
});
