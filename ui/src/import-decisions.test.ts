import { expect, it } from "vitest";
import type { Difference, Preview } from "@/api/types";
import { reconcileChoices, sameDifference } from "./import-decisions";

const difference: Difference = {
  row: 2,
  id: 1,
  name: "王明",
  field: "title",
  affiliation: { university: "北京大学", school: null, department: null, title: null },
  local: "讲师",
  incoming: "教授",
};

it("retains a choice across new objects and row changes, and drops changed comparisons", () => {
  expect(sameDifference(difference, structuredClone(difference))).toBe(true);
  expect(sameDifference(difference, { ...difference, row: 3, name: "新姓名" })).toBe(true);
  for (const changed of [
    { id: 2 },
    { field: "note" as const },
    { local: "副教授" },
    { incoming: "研究员" },
    { affiliation: null },
    { affiliation: { ...difference.affiliation, university: "清华大学", school: null, department: null, title: null } },
  ]) {
    expect(sameDifference(difference, { ...difference, ...changed })).toBe(false);
  }
  const current = { ...difference, row: 3 };
  const preview: Preview = {
    sheets: ["导师"],
    sheet: 0,
    columns: [],
    issue: null,
    created: 0,
    supplemented: 0,
    updated: 1,
    unchanged: 0,
    differences: [current, { ...difference, id: 2 }],
    maybeSame: [],
    trashed: [{ id: 7, name: "王明", rows: [2, 3] }],
    errors: [],
  };
  expect(reconcileChoices(preview, { take: [structuredClone(difference)], separate: [2], restore: [7, 8] })).toEqual({
    take: [current],
    separate: [],
    restore: [7],
  });
});
