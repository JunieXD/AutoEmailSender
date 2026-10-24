import { expect, it } from "vitest";
import type { Difference, Preview } from "@/api/types";
import { reconcileChoices, reconcileMatches, sameDifference } from "./import-decisions";

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
  expect(
    reconcileChoices(
      preview,
      { take: [structuredClone(difference)], matches: [{ row: 2, id: null }], restore: [7, 8] },
      preview,
    ),
  ).toEqual({
    take: [current],
    matches: [],
    restore: [7],
  });
});

it("clears group choices on sheet changes and checks candidate identity after remapping columns", () => {
  const candidate = {
    row: 2,
    rows: [2, 3],
    incoming: { name: "王明", affiliations: [], emails: [], research: null, homepages: [] },
    candidates: [
      { id: 7, profile: { name: "王明", affiliations: [], emails: [], research: null, homepages: [] } },
      { id: 8, profile: { name: "王明", affiliations: [], emails: [], research: null, homepages: [] } },
    ],
    selected: 7,
  };
  const previous: Preview = {
    sheets: ["第一张", "第二张"],
    sheet: 0,
    columns: [],
    issue: null,
    created: 0,
    supplemented: 0,
    updated: 1,
    unchanged: 0,
    differences: [difference],
    maybeSame: [candidate],
    trashed: [{ id: 7, name: "王明", rows: [2, 3] }],
    errors: [],
  };
  const choices = { take: [difference], matches: [{ row: 2, id: null }], restore: [7] };
  const merging = { ...choices, matches: [{ row: 2, id: 8 }] };
  expect(reconcileChoices(structuredClone(previous), merging, previous)).toEqual(merging);
  expect(reconcileChoices(previous, { ...choices, matches: [{ row: 2, id: 9 }] }, previous).matches).toEqual([]);
  expect(reconcileChoices(structuredClone(previous), choices, previous)).toEqual(choices);
  expect(reconcileChoices({ ...previous, sheet: 1 }, choices, previous)).toEqual({
    take: [difference],
    matches: [],
    restore: [],
  });
  for (const changed of [
    { candidates: candidate.candidates.slice(1) },
    { row: 4, rows: [4, 5] },
    { rows: [2, 4] },
    { rows: [2] },
  ]) {
    expect(
      reconcileChoices({ ...previous, maybeSame: [{ ...candidate, ...changed }] }, choices, previous).matches,
    ).toEqual([]);
  }
});

it("retains restore approval for a non-default target when unrelated columns change", () => {
  const profile = { name: "王伟", affiliations: [], emails: [], research: "数论", homepages: [] };
  const group = {
    row: 2,
    rows: [2],
    incoming: profile,
    candidates: [
      { id: 1, profile: { ...profile, research: "图学习" } },
      { id: 2, profile: { ...profile, research: "代数学" } },
    ],
    selected: 2,
  };
  const previous: Preview = {
    sheets: ["导师"],
    sheet: 0,
    columns: [
      { header: "姓名", field: "name" },
      { header: "学校", field: "university" },
      { header: "研究方向", field: "research" },
      { header: "备注", field: "note" },
    ],
    issue: null,
    created: 0,
    supplemented: 1,
    updated: 1,
    unchanged: 0,
    differences: [],
    maybeSame: [group],
    trashed: [{ id: 2, name: "王伟", rows: [2] }],
    errors: [],
  };
  const basis: Preview = {
    ...previous,
    columns: previous.columns.map((c) => (c.field === "note" ? { ...c, field: null } : c)),
    created: 1,
    supplemented: 0,
    updated: 0,
    maybeSame: [{ ...group, selected: null }],
    trashed: [],
  };
  const choices = { take: [], matches: [{ row: 2, id: 2 }], restore: [2] };
  const pending = { ...choices, matches: reconcileMatches(basis, choices, previous) };
  const applied = { ...basis, created: 0, updated: 1, maybeSame: [group], trashed: previous.trashed };
  expect(reconcileChoices(applied, pending, basis)).toEqual(choices);
  expect(reconcileChoices({ ...applied, trashed: [] }, pending, basis).restore).toEqual([]);
});
