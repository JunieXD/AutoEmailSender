import { expect, it } from "vitest";
import type { Affiliation } from "@/api/types";
import { mergedAffiliationCount } from "./affiliation";

const place = (school: string | null, department: string | null = null): Affiliation => ({
  university: "北京大学",
  school,
  department,
  title: null,
});

it("counts compatible affiliations once, fills blanks and ignores title conflicts", () => {
  const kept = [{ ...place(null), title: "副教授" }];
  const incoming = [{ ...place("计算机学院"), title: "教授" }, place("计算机学院", "计算机系"), place("软件学院")];
  expect(mergedAffiliationCount(kept, incoming)).toBe(2);
  expect(kept[0]?.school).toBeNull();
});

it("keeps distinct departments and matches complete units before partial ones", () => {
  const kept = [place(null), place("计算机学院", "计算机系")];
  expect(mergedAffiliationCount(kept, [place("计算机学院", "计算机系"), place("软件学院", "软件系")])).toBe(2);
  expect(mergedAffiliationCount([place("计算机学院", "计算机系")], [place("计算机学院", "人工智能系")])).toBe(2);
});
