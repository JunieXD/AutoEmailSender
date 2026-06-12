import { describe, expect, it } from "vitest";
import type { ProfessorManagementItemDTO } from "@/types";
import {
  sortManagementProfessors,
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
) => sortManagementProfessors(professors, sortKey).map((professor) => professor.name);

describe("sortManagementProfessors", () => {
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

  it("sorts by updated time descending", () => {
    expect(namesFor(professors, "updatedAtDesc")).toEqual([
      "Carol",
      "Bob",
      "Alice",
    ]);
  });

  it("sorts by name ascending", () => {
    expect(namesFor(professors, "nameAsc")).toEqual(["Alice", "Bob", "Carol"]);
  });

  it("sorts by university and keeps empty university last", () => {
    expect(namesFor(professors, "universityAsc")).toEqual([
      "Alice",
      "Bob",
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
