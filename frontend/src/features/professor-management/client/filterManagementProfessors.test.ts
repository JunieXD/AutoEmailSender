import { describe, expect, it } from "vitest";
import type { ProfessorManagementItemDTO } from "@/types";
import {
  DEFAULT_MANAGEMENT_KEYWORD_SEARCH_SCOPES,
  buildManagementFilterOptions,
  createDefaultManagementFilters,
  filterManagementProfessors,
  getManagementKeywordSearchPlaceholder,
  getActiveManagementAdvancedFilterCount,
  normalizeManagementKeywordSearchScopes,
  NO_FIELD_FILTER_VALUE,
  NO_TAG_FILTER_VALUE,
  pruneManagementFilters,
  type ProfessorManagementFilterState,
  type ProfessorManagementKeywordSearchScope,
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
        keywordSearchScopes: ["name"],
      }),
    ).toEqual(["副主任"]);
    expect(
      namesFor(scopedProfessors, {
        keyword: "副",
        keywordSearchScopes: ["title"],
      }),
    ).toEqual(["Normal"]);
  });

  it("supports email-only keyword matching on management page", () => {
    expect(
      namesFor(professors, {
        keyword: "bob@example.edu",
        keywordSearchScopes: ["email"],
      }),
    ).toEqual(["Bob"]);
    expect(
      namesFor(professors, {
        keyword: "bob@example.edu",
        keywordSearchScopes: ["name"],
      }),
    ).toEqual([]);
  });

  it("uses the exact keyword 无 to match missing selected fields", () => {
    const missingFieldCases: Array<
      [
        ProfessorManagementKeywordSearchScope,
        Partial<ProfessorManagementItemDTO>,
        Partial<ProfessorManagementItemDTO>,
      ]
    > = [
      ["email", { email: null }, { email: "filled@example.edu" }],
      ["university", { university: null }, { university: "MIT" }],
      ["school", { school: null }, { school: "Engineering" }],
      ["department", { department: null }, { department: "EECS" }],
      ["title", { title: null }, { title: "教授" }],
      [
        "researchDirection",
        { research_direction: null },
        { research_direction: "AI systems" },
      ],
      [
        "tag",
        { tags: [] },
        {
          tags: [
            {
              id: 1,
              name: "重点跟进",
              text_color: "#166534",
              background_color: "#dcfce7",
            },
          ],
        },
      ],
    ];

    missingFieldCases.forEach(([scope, missingField, filledField], index) => {
      const missingName = `Missing ${scope}`;
      const candidates = [
        buildProfessor({ id: index * 2 + 10, name: missingName, ...missingField }),
        buildProfessor({ id: index * 2 + 11, name: `Filled ${scope}`, ...filledField }),
      ];

      expect(
        namesFor(candidates, {
          keyword: " 无 ",
          keywordSearchScopes: [scope],
        }),
      ).toEqual([missingName]);
    });
  });

  it("treats 无 as an empty-field query without changing longer keyword searches", () => {
    const candidates = [
      buildProfessor({
        id: 30,
        name: "Missing direction",
        research_direction: "   ",
      }),
      buildProfessor({
        id: 31,
        name: "Drone research",
        research_direction: "无人机系统",
      }),
    ];

    expect(
      namesFor(candidates, {
        keyword: "无",
        keywordSearchScopes: ["researchDirection"],
      }),
    ).toEqual(["Missing direction"]);
    expect(
      namesFor(candidates, {
        keyword: "无人机",
        keywordSearchScopes: ["researchDirection"],
      }),
    ).toEqual(["Drone research"]);
  });

  it("drops invalid management search scopes and keeps valid selections", () => {
    expect(normalizeManagementKeywordSearchScopes(["email", "unknown"])).toEqual([
      "email",
    ]);
    expect(normalizeManagementKeywordSearchScopes(["unknown"])).toEqual(
      DEFAULT_MANAGEMENT_KEYWORD_SEARCH_SCOPES,
    );
  });

  it("builds management keyword placeholder from selected search scopes", () => {
    expect(getManagementKeywordSearchPlaceholder(["email"])).toBe("邮箱");
    expect(getManagementKeywordSearchPlaceholder(["name", "email"])).toBe(
      "姓名、邮箱",
    );
    expect(getManagementKeywordSearchPlaceholder(["unknown"])).toBe(
      "姓名、邮箱、学校、学院、系所、职称、研究方向、标签",
    );
  });

  it("matches keyword against professor tag names", () => {
    const taggedProfessors = [
      buildProfessor({
        id: 4,
        name: "Tagged",
        tags: [
          {
            id: 1,
            name: "高意愿",
            text_color: "#166534",
            background_color: "#dcfce7",
          },
        ],
      }),
    ];

    expect(namesFor(taggedProfessors, { keyword: "高意愿" })).toEqual(["Tagged"]);
  });

  it("does not match keyword against personal notes", () => {
    const noteOnlyProfessors = [
      buildProfessor({
        id: 4,
        name: "Alice",
        email: "alice@example.edu",
        university: "MIT",
        school: "Engineering",
        research_direction: "Robotics",
        personal_note: "隐私备注关键词",
      }),
      buildProfessor({
        id: 5,
        name: "Bob",
        email: "bob@example.edu",
        personal_note: null,
      }),
    ];

    expect(
      namesFor(noteOnlyProfessors, { keyword: "隐私备注关键词" }),
    ).toEqual([]);
  });

  it("limits management keyword matching to selected tag scope", () => {
    const taggedProfessors = [
      buildProfessor({
        id: 4,
        name: "高意愿导师",
        title: "教授",
        tags: [
          {
            id: 1,
            name: "重点跟进",
            text_color: "#166534",
            background_color: "#dcfce7",
          },
        ],
      }),
      buildProfessor({
        id: 5,
        name: "普通导师",
        title: "重点跟进",
        tags: [],
      }),
    ];

    expect(
      namesFor(taggedProfessors, {
        keyword: "重点跟进",
        keywordSearchScopes: ["tag"],
      }),
    ).toEqual(["高意愿导师"]);
    expect(
      namesFor(taggedProfessors, {
        keyword: "重点跟进",
        keywordSearchScopes: ["title"],
      }),
    ).toEqual(["普通导师"]);
  });

  it("filters by selected tag ids and no-tag virtual option", () => {
    const taggedProfessors = [
      buildProfessor({
        id: 4,
        name: "Tagged",
        tags: [
          {
            id: 1,
            name: "高意愿",
            text_color: "#166534",
            background_color: "#dcfce7",
          },
        ],
      }),
      buildProfessor({ id: 5, name: "No Tag", tags: [] }),
    ];

    expect(namesFor(taggedProfessors, { tagIds: ["1"] })).toEqual(["Tagged"]);
    expect(namesFor(taggedProfessors, { tagIds: [NO_TAG_FILTER_VALUE] })).toEqual([
      "No Tag",
    ]);
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

  it("filters nullable fields with the no-value option", () => {
    const sparselyProfiledProfessor = buildProfessor({
      id: 4,
      name: "Missing",
      title: null,
      university: null,
      school: null,
      department: null,
    });
    const completeProfessor = buildProfessor({
      id: 5,
      name: "Complete",
      title: "教授",
      university: "MIT",
      school: "School of Engineering",
      department: "EECS",
    });
    const profs = [sparselyProfiledProfessor, completeProfessor];

    expect(namesFor(profs, { universities: [NO_FIELD_FILTER_VALUE] })).toEqual([
      "Missing",
    ]);
    expect(namesFor(profs, { schools: [NO_FIELD_FILTER_VALUE] })).toEqual([
      "Missing",
    ]);
    expect(namesFor(profs, { departments: [NO_FIELD_FILTER_VALUE] })).toEqual([
      "Missing",
    ]);
    expect(namesFor(profs, { titles: [NO_FIELD_FILTER_VALUE] })).toEqual([
      "Missing",
    ]);
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
      tagIds: ["404"],
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
      tagIds: [],
    });

    expect(schoolPruned.departments).toEqual(["Robotics"]);
    expect(pruned.titles).toEqual(["教授"]);
    expect(pruned.tagIds).toEqual([]);
  });

  it("keeps no-value selections while pruning dependent options", () => {
    const pruned = pruneManagementFilters(
      [buildProfessor({ id: 4, name: "Missing" })],
      {
        ...createDefaultManagementFilters(),
        universities: [NO_FIELD_FILTER_VALUE],
        schools: [NO_FIELD_FILTER_VALUE],
        departments: [NO_FIELD_FILTER_VALUE],
        titles: [NO_FIELD_FILTER_VALUE],
      },
    );

    expect(pruned.universities).toEqual([NO_FIELD_FILTER_VALUE]);
    expect(pruned.schools).toEqual([NO_FIELD_FILTER_VALUE]);
    expect(pruned.departments).toEqual([NO_FIELD_FILTER_VALUE]);
    expect(pruned.titles).toEqual([NO_FIELD_FILTER_VALUE]);
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
