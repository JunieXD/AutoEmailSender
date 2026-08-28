import { describe, expect, it } from "vitest";
import type { ProfessorDashboardItemDTO } from "@/types";
import {
  DEFAULT_PROFESSOR_DASHBOARD_SORT_KEY,
  PROFESSOR_DASHBOARD_SORT_OPTIONS,
  sortDashboardProfessors,
  type ProfessorDashboardSortDirection,
  type ProfessorDashboardSortKey,
} from "./sortDashboardProfessors";

const buildProfessor = (
  overrides: Partial<ProfessorDashboardItemDTO>,
): ProfessorDashboardItemDTO => ({
  id: 1,
  name: "Default",
  email: null,
  title: null,
  university: null,
  school: null,
  department: null,
  research_direction: null,
  personal_note: null,
  recent_papers: [],
  match_score: null,
  sent_count: 0,
  status: "not_contacted",
  last_sent_at: null,
  last_replied_at: null,
  updated_at: "2026-05-01T00:00:00Z",
  tags: [],
  ...overrides,
});

const namesFor = (
  sortKey: ProfessorDashboardSortKey,
  professors: ProfessorDashboardItemDTO[],
  direction?: ProfessorDashboardSortDirection,
) =>
  sortDashboardProfessors(professors, sortKey, direction).map(
    (professor) => professor.name,
  );

describe("sortDashboardProfessors", () => {
  it("defaults to recently updated professors first", () => {
    expect(DEFAULT_PROFESSOR_DASHBOARD_SORT_KEY).toBe("updatedAtDesc");
  });

  it("uses neutral field labels because direction is selected separately", () => {
    expect(PROFESSOR_DASHBOARD_SORT_OPTIONS).toEqual([
      { value: "latest", label: "导入时间" },
      { value: "updatedAtDesc", label: "更新时间" },
      { value: "matchScoreDesc", label: "匹配度" },
      { value: "sentCountDesc", label: "发送次数" },
      { value: "nameAsc", label: "姓名" },
      { value: "lastSentAt", label: "发送时间" },
      { value: "lastRepliedAt", label: "回复时间" },
    ]);
  });

  it("keeps backend order for latest import", () => {
    const professors = [
      buildProfessor({ id: 1, name: "First" }),
      buildProfessor({ id: 2, name: "Second" }),
      buildProfessor({ id: 3, name: "Third" }),
    ];

    expect(namesFor("latest", professors)).toEqual(["First", "Second", "Third"]);
  });

  it("reverses backend order for latest import ascending", () => {
    const professors = [
      buildProfessor({ id: 1, name: "First" }),
      buildProfessor({ id: 2, name: "Second" }),
      buildProfessor({ id: 3, name: "Third" }),
    ];

    expect(namesFor("latest", professors, "asc")).toEqual([
      "Third",
      "Second",
      "First",
    ]);
  });

  it("sorts by updated time in either direction", () => {
    const professors = [
      buildProfessor({
        id: 1,
        name: "Old",
        updated_at: "2026-06-01T09:00:00Z",
      }),
      buildProfessor({
        id: 2,
        name: "New",
        updated_at: "2026-06-02T09:00:00Z",
      }),
    ];

    expect(namesFor("updatedAtDesc", professors)).toEqual(["New", "Old"]);
    expect(namesFor("updatedAtDesc", professors, "asc")).toEqual(["Old", "New"]);
  });

  it("sorts by match score descending and places null scores last", () => {
    const professors = [
      buildProfessor({ id: 1, name: "Unscored", match_score: null }),
      buildProfessor({ id: 2, name: "Strong", match_score: 92 }),
      buildProfessor({ id: 3, name: "Medium", match_score: 76 }),
    ];

    expect(namesFor("matchScoreDesc", professors)).toEqual([
      "Strong",
      "Medium",
      "Unscored",
    ]);
  });

  it("sorts by match score ascending and places null scores last", () => {
    const professors = [
      buildProfessor({ id: 1, name: "Unscored", match_score: null }),
      buildProfessor({ id: 2, name: "Strong", match_score: 92 }),
      buildProfessor({ id: 3, name: "Medium", match_score: 76 }),
    ];

    expect(namesFor("matchScoreDesc", professors, "asc")).toEqual([
      "Medium",
      "Strong",
      "Unscored",
    ]);
  });

  it("sorts by sent count descending", () => {
    const professors = [
      buildProfessor({ id: 1, name: "None", sent_count: 0 }),
      buildProfessor({ id: 2, name: "Many", sent_count: 4 }),
      buildProfessor({ id: 3, name: "One", sent_count: 1 }),
    ];

    expect(namesFor("sentCountDesc", professors)).toEqual(["Many", "One", "None"]);
  });

  it("sorts by sent count ascending", () => {
    const professors = [
      buildProfessor({ id: 1, name: "None", sent_count: 0 }),
      buildProfessor({ id: 2, name: "Many", sent_count: 4 }),
      buildProfessor({ id: 3, name: "One", sent_count: 1 }),
    ];

    expect(namesFor("sentCountDesc", professors, "asc")).toEqual([
      "None",
      "One",
      "Many",
    ]);
  });

  it("sorts names ascending", () => {
    const professors = [
      buildProfessor({ id: 1, name: "Zhang" }),
      buildProfessor({ id: 2, name: "Alice" }),
      buildProfessor({ id: 3, name: "Bob" }),
    ];

    expect(namesFor("nameAsc", professors)).toEqual(["Alice", "Bob", "Zhang"]);
  });

  it("sorts names descending", () => {
    const professors = [
      buildProfessor({ id: 1, name: "Zhang" }),
      buildProfessor({ id: 2, name: "Alice" }),
      buildProfessor({ id: 3, name: "Bob" }),
    ];

    expect(namesFor("nameAsc", professors, "desc")).toEqual([
      "Zhang",
      "Bob",
      "Alice",
    ]);
  });

  it("sorts by sent time descending and keeps missing times last", () => {
    const professors = [
      buildProfessor({ id: 1, name: "Missing" }),
      buildProfessor({
        id: 2,
        name: "Old",
        last_sent_at: "2026-06-01T09:00:00Z",
      }),
      buildProfessor({
        id: 3,
        name: "New",
        last_sent_at: "2026-06-01T12:00:00Z",
      }),
    ];

    expect(namesFor("lastSentAt", professors, "desc")).toEqual([
      "New",
      "Old",
      "Missing",
    ]);
  });

  it("sorts by replied time ascending and keeps missing times last", () => {
    const professors = [
      buildProfessor({ id: 1, name: "Missing" }),
      buildProfessor({
        id: 2,
        name: "Late",
        last_replied_at: "2026-06-02T12:00:00Z",
      }),
      buildProfessor({
        id: 3,
        name: "Early",
        last_replied_at: "2026-06-01T12:00:00Z",
      }),
    ];

    expect(namesFor("lastRepliedAt", professors, "asc")).toEqual([
      "Early",
      "Late",
      "Missing",
    ]);
  });

  it("keeps missing time professors in stable order", () => {
    const professors = [
      buildProfessor({ id: 1, name: "Missing A" }),
      buildProfessor({
        id: 2,
        name: "Timed",
        last_replied_at: "2026-06-01T12:00:00Z",
      }),
      buildProfessor({ id: 3, name: "Missing B" }),
    ];

    expect(namesFor("lastRepliedAt", professors, "desc")).toEqual([
      "Timed",
      "Missing A",
      "Missing B",
    ]);
  });

  it("does not mutate the input array", () => {
    const professors = [
      buildProfessor({ id: 1, name: "Unscored", match_score: null }),
      buildProfessor({ id: 2, name: "Strong", match_score: 92 }),
    ];

    sortDashboardProfessors(professors, "matchScoreDesc");

    expect(professors.map((professor) => professor.name)).toEqual([
      "Unscored",
      "Strong",
    ]);
  });
});
