import { describe, expect, it } from "vitest";
import type { ProfessorManagementItemDTO } from "@/types";
import {
  DEFAULT_MANAGEMENT_KEYWORD_FIELDS,
  buildManagementFilterOptions,
  createDefaultManagementFilters,
  filterManagementProfessors,
  getActiveManagementAdvancedFilterCount,
  normalizeManagementKeywordFields,
  pruneManagementFilters,
  type ProfessorManagementFilterState,
} from "./filterManagementProfessors";

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
  recent_papers: [],
  profile_url: null,
  source_url: null,
  crawl_status: "manual",
  skip_reason: null,
  archived_at: null,
  created_at: "2026-05-01T00:00:00",
  updated_at: "2026-05-01T00:00:00",
  ...overrides,
});

const namesFor = (
  professors: ProfessorManagementItemDTO[],
  overrides: Partial<ProfessorManagementFilterState>,
) =>
  filterManagementProfessors(professors, {
    ...createDefaultManagementFilters(),
    ...overrides,
  }).map((professor) => professor.name);

describe("filterManagementProfessors", () => {
  const professors = [
    buildProfessor({
      id: 1,
      name: "Alice",
      email: "alice@example.edu",
      title: "教授 / 博导",
      university: "MIT",
      school: "School of Engineering",
      department: "EECS",
      research_direction: "AI systems",
    }),
    buildProfessor({
      id: 2,
      name: "Bob",
      email: "bob@example.edu",
      title: "副教授",
      university: "Stanford",
      school: "School of Medicine",
      department: "Bioengineering",
      research_direction: "Biomedical AI",
    }),
    buildProfessor({
      id: 3,
      name: "Carol",
      email: "carol@example.edu",
      title: "助理教授",
      university: "MIT",
      school: "AI Institute",
      department: "Robotics",
      research_direction: "Robotics planning",
    }),
  ];

  it("matches keyword against email, school, department, title, and research direction", () => {
    expect(namesFor(professors, { keyword: "robotics" })).toEqual(["Carol"]);
    expect(namesFor(professors, { keyword: "bob@example.edu" })).toEqual(["Bob"]);
    expect(namesFor(professors, { keyword: "School of Medicine" })).toEqual([
      "Bob",
    ]);
    expect(namesFor(professors, { keyword: "博导" })).toEqual(["Alice"]);
  });

  it("limits keyword matching to selected management fields", () => {
    const scopedProfessors = [
      buildProfessor({
        id: 4,
        name: "副主任",
        email: "director@example.edu",
        title: "教授",
      }),
      buildProfessor({
        id: 5,
        name: "Normal",
        email: "normal@example.edu",
        title: "副教授",
      }),
    ];

    expect(
      namesFor(scopedProfessors, {
        keyword: "副",
        keywordFields: ["name"],
      }),
    ).toEqual(["副主任"]);
    expect(
      namesFor(scopedProfessors, {
        keyword: "副",
        keywordFields: ["title"],
      }),
    ).toEqual(["Normal"]);
  });

  it("supports email-only keyword matching on management page", () => {
    expect(
      namesFor(professors, {
        keyword: "bob@example.edu",
        keywordFields: ["email"],
      }),
    ).toEqual(["Bob"]);
    expect(
      namesFor(professors, {
        keyword: "bob@example.edu",
        keywordFields: ["name"],
      }),
    ).toEqual([]);
  });

  it("normalizes invalid management keyword fields to the default scope", () => {
    expect(normalizeManagementKeywordFields(["name", "unknown"])).toEqual(
      DEFAULT_MANAGEMENT_KEYWORD_FIELDS,
    );
    expect(normalizeManagementKeywordFields(["unknown"])).toEqual(
      DEFAULT_MANAGEMENT_KEYWORD_FIELDS,
    );
  });

  it("uses OR within one multi-select group and AND across groups", () => {
    expect(
      namesFor(professors, {
        universities: ["MIT", "Stanford"],
      }),
    ).toEqual(["Alice", "Bob", "Carol"]);

    expect(
      namesFor(professors, {
        universities: ["MIT"],
        schools: ["AI Institute"],
        departments: ["Robotics"],
        titles: ["助理教授"],
      }),
    ).toEqual(["Carol"]);
  });

  it("builds sorted non-empty options and limits schools to selected universities", () => {
    const options = buildManagementFilterOptions([
      ...professors,
      buildProfessor({
        id: 4,
        name: "Empty",
        university: "",
        school: null,
      }),
    ]);

    expect(options.universities).toEqual(["MIT", "Stanford"]);
    expect(options.schools).toEqual([
      "AI Institute",
      "School of Engineering",
      "School of Medicine",
    ]);
    expect(options.departments).toEqual(["Bioengineering", "EECS", "Robotics"]);
    expect(options.titles).toEqual(["博导", "副教授", "教授", "助理教授"]);

    const limitedOptions = buildManagementFilterOptions(professors, {
      universities: ["MIT"],
    });

    expect(limitedOptions.schools).toEqual([
      "AI Institute",
      "School of Engineering",
    ]);
  });

  it("limits departments to selected universities and schools", () => {
    const mitOptions = buildManagementFilterOptions(professors, {
      universities: ["MIT"],
      schools: [],
    });

    expect(mitOptions.departments).toEqual(["EECS", "Robotics"]);

    const instituteOptions = buildManagementFilterOptions(professors, {
      universities: ["MIT"],
      schools: ["AI Institute"],
    });

    expect(instituteOptions.departments).toEqual(["Robotics"]);
  });
  it("matches selected options against trimmed management fields", () => {
    const professorsWithWhitespace = [
      buildProfessor({
        id: 4,
        name: "Whitespace",
        title: " 教授 ",
        university: " MIT ",
        school: " AI Institute ",
        department: " Robotics ",
      }),
    ];

    expect(
      namesFor(professorsWithWhitespace, {
        universities: ["MIT"],
        schools: ["AI Institute"],
        departments: ["Robotics"],
        titles: ["教授"],
      }),
    ).toEqual(["Whitespace"]);
  });

  it("counts active advanced filters", () => {
    expect(
      getActiveManagementAdvancedFilterCount({
        ...createDefaultManagementFilters(),
        universities: ["MIT"],
        titles: ["教授", "副教授"],
      }),
    ).toBe(3);
  });

  it("prunes filters when universities or options disappear", () => {
    const pruned = pruneManagementFilters(professors, {
      ...createDefaultManagementFilters(),
      keyword: "",
      universities: ["MIT", "Unknown"],
      schools: ["AI Institute", "School of Medicine"],
      departments: ["EECS", "Unknown"],
      titles: ["教授", "不存在"],
    });

    expect(pruned.universities).toEqual(["MIT"]);
    expect(pruned.schools).toEqual(["AI Institute"]);
    expect(pruned.departments).toEqual([]);

    const schoolPruned = pruneManagementFilters(professors, {
      ...createDefaultManagementFilters(),
      keyword: "",
      universities: ["MIT"],
      schools: ["AI Institute"],
      departments: ["EECS", "Robotics"],
      titles: [],
    });

    expect(schoolPruned.departments).toEqual(["Robotics"]);
    expect(pruned.titles).toEqual(["教授"]);
  });

  it("does not mutate the input array", () => {
    const input = [...professors];
    filterManagementProfessors(input, {
      ...createDefaultManagementFilters(),
      universities: ["MIT"],
    });

    expect(input.map((professor) => professor.name)).toEqual([
      "Alice",
      "Bob",
      "Carol",
    ]);
  });
});
