import { describe, expect, it } from "vitest";
import type { ProfessorManagementItemDTO } from "@/types";
import {
  PROFESSOR_MANAGEMENT_SORT_OPTIONS,
  sortManagementProfessors,
  type ProfessorManagementSortDirection,
  type ProfessorManagementSortKey,
} from "./sortManagementProfessors";

const buildProfessor = (
  overrides: Partial<ProfessorManagementItemDTO>,
): ProfessorManagementItemDTO => ({
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
  profile_url: null,
  source_url: null,
  crawl_status: "manual",
  skip_reason: null,
  archived_at: null,
  created_at: "2026-05-01T00:00:00",
  updated_at: "2026-05-01T00:00:00",
  tags: [],
  ...overrides,
});

const namesFor = (
  professors: ProfessorManagementItemDTO[],
  sortKey: ProfessorManagementSortKey,
  direction?: ProfessorManagementSortDirection,
) =>
  sortManagementProfessors(professors, sortKey, direction).map(
    (professor) => professor.name,
  );

describe("sortManagementProfessors", () => {
  it("uses neutral field labels because direction is selected separately", () => {
    expect(PROFESSOR_MANAGEMENT_SORT_OPTIONS).toEqual([
      { value: "latest", label: "导入时间" },
      { value: "updatedAtDesc", label: "更新时间" },
      { value: "nameAsc", label: "姓名" },
      { value: "universityAsc", label: "学校" },
    ]);
  });

  const professors = [
    buildProfessor({
      id: 1,
      name: "Carol",
      university: null,
      created_at: "2026-05-01T00:00:00",
      updated_at: "2026-05-03T00:00:00",
    }),
    buildProfessor({
      id: 2,
      name: "Alice",
      university: "MIT",
      created_at: "2026-05-03T00:00:00",
      updated_at: "2026-05-01T00:00:00",
    }),
    buildProfessor({
      id: 3,
      name: "Bob",
      university: "Stanford",
      created_at: "2026-05-02T00:00:00",
      updated_at: "2026-05-02T00:00:00",
    }),
  ];

  it("sorts by latest imported first", () => {
    expect(namesFor(professors, "latest")).toEqual(["Alice", "Bob", "Carol"]);
  });

  it("sorts by earliest imported first", () => {
    expect(namesFor(professors, "latest", "asc")).toEqual([
      "Carol",
      "Bob",
      "Alice",
    ]);
  });

  it("sorts by updated time descending", () => {
    expect(namesFor(professors, "updatedAtDesc")).toEqual([
      "Carol",
      "Bob",
      "Alice",
    ]);
  });

  it("sorts by updated time ascending", () => {
    expect(namesFor(professors, "updatedAtDesc", "asc")).toEqual([
      "Alice",
      "Bob",
      "Carol",
    ]);
  });

  it("sorts by name ascending", () => {
    expect(namesFor(professors, "nameAsc")).toEqual(["Alice", "Bob", "Carol"]);
  });

  it("sorts by name descending", () => {
    expect(namesFor(professors, "nameAsc", "desc")).toEqual([
      "Carol",
      "Bob",
      "Alice",
    ]);
  });

  it("sorts by university and keeps empty university last", () => {
    expect(namesFor(professors, "universityAsc")).toEqual([
      "Alice",
      "Bob",
      "Carol",
    ]);
  });

  it("sorts by university descending and keeps empty university last", () => {
    expect(namesFor(professors, "universityAsc", "desc")).toEqual([
      "Bob",
      "Alice",
      "Carol",
    ]);
  });

  it("does not mutate the input array", () => {
    const input = [...professors];
    sortManagementProfessors(input, "latest");

    expect(input.map((professor) => professor.name)).toEqual([
      "Carol",
      "Alice",
      "Bob",
    ]);
  });
});
