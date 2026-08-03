import { describe, expect, it } from "vitest";
import type { ProfessorDashboardItemDTO } from "@/types";
import {
  DEFAULT_DASHBOARD_KEYWORD_SEARCH_SCOPES,
  buildDashboardFilterOptions,
  createDefaultDashboardFilters,
  getActiveDashboardFilterCount,
  filterDashboardProfessors,
  getDashboardKeywordSearchPlaceholder,
  normalizeDashboardKeywordSearchScopes,
  NO_FIELD_FILTER_VALUE,
  NO_MATCH_SCORE_FILTER_VALUE,
  NO_TAG_FILTER_VALUE,
  pruneDashboardFilters,
  type DashboardFilterState,
} from "./filterDashboardProfessors";

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
  tags: [],
  ...overrides,
});

const namesFor = (
  professors: ProfessorDashboardItemDTO[],
  overrides: Partial<DashboardFilterState>,
) =>
  filterDashboardProfessors(professors, {
    ...createDefaultDashboardFilters(),
    ...overrides,
  }).map((professor) => professor.name);

describe("filterDashboardProfessors", () => {
  const professors = [
    buildProfessor({
      id: 1,
      name: "Alice",
      title: "教授",
      university: "MIT",
      school: "School of Engineering",
      department: "EECS",
      research_direction: "AI systems",
      match_score: 91,
      status: "ready_to_send",
    }),
    buildProfessor({
      id: 2,
      name: "Bob",
      title: "副教授",
      university: "Stanford",
      school: "School of Medicine",
      department: "Bioengineering",
      research_direction: "Biomedical AI",
      match_score: 76,
      status: "not_contacted",
    }),
    buildProfessor({
      id: 3,
      name: "Carol",
      title: "助理教授",
      university: "MIT",
      school: "AI Institute",
      department: "Robotics",
      research_direction: "Robotics planning",
      match_score: null,
      status: "replied",
      has_active_schedule: true,
    }),
  ];

  it("filters active schedules without replacing the relationship status", () => {
    expect(namesFor(professors, { statuses: ["scheduled"] })).toEqual(["Carol"]);
    expect(namesFor(professors, { statuses: ["replied"] })).toEqual(["Carol"]);
  });

  it("matches keyword against school, department, title, and research direction", () => {
    expect(namesFor(professors, { keyword: "robotics" })).toEqual(["Carol"]);
    expect(namesFor(professors, { keyword: "School of Medicine" })).toEqual(["Bob"]);
    expect(namesFor(professors, { keyword: "教授" })).toEqual([
      "Alice",
      "Bob",
      "Carol",
    ]);
  });

  it("limits keyword matching to selected dashboard fields", () => {
    const scopedProfessors = [
      buildProfessor({ id: 4, name: "副主任", title: "教授" }),
      buildProfessor({ id: 5, name: "Normal", title: "副教授" }),
    ];

    expect(namesFor(scopedProfessors, { keyword: "副" })).toEqual([
      "副主任",
      "Normal",
    ]);
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

  it("ignores dashboard search scopes when keyword is empty", () => {
    expect(
      namesFor(professors, {
        keyword: "",
        keywordSearchScopes: ["name"],
      }),
    ).toEqual(["Alice", "Bob", "Carol"]);
  });

  it("uses the exact keyword 无 to match missing selected fields", () => {
    const candidates = [
      buildProfessor({
        id: 4,
        name: "Missing direction",
        research_direction: null,
      }),
      buildProfessor({
        id: 5,
        name: "Blank direction",
        research_direction: "   ",
      }),
      buildProfessor({
        id: 6,
        name: "Drone research",
        research_direction: "无人机系统",
      }),
    ];

    expect(
      namesFor(candidates, {
        keyword: " 无 ",
        keywordSearchScopes: ["researchDirection"],
      }),
    ).toEqual(["Missing direction", "Blank direction"]);
    expect(
      namesFor(candidates, {
        keyword: "无人机",
        keywordSearchScopes: ["researchDirection"],
      }),
    ).toEqual(["Drone research"]);
  });

  it("drops invalid dashboard search scopes and keeps valid selections", () => {
    const fields = normalizeDashboardKeywordSearchScopes([
      "researchDirection",
      "unknown",
    ]);

    expect(fields).toEqual(["researchDirection"]);
    expect(normalizeDashboardKeywordSearchScopes(["unknown"])).toEqual(
      DEFAULT_DASHBOARD_KEYWORD_SEARCH_SCOPES,
    );
  });

  it("builds dashboard keyword placeholder from selected search scopes", () => {
    expect(getDashboardKeywordSearchPlaceholder(["name"])).toBe("姓名");
    expect(getDashboardKeywordSearchPlaceholder(["name", "title"])).toBe(
      "姓名、职称",
    );
    expect(getDashboardKeywordSearchPlaceholder(["unknown"])).toBe(
      "姓名、学校、学院、系所、职称、研究方向、标签",
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
        university: "MIT",
        school: "Engineering",
        research_direction: "Robotics",
        personal_note: "独有备注关键词",
      }),
      buildProfessor({
        id: 5,
        name: "Bob",
        personal_note: null,
      }),
    ];

    expect(
      namesFor(noteOnlyProfessors, { keyword: "独有备注关键词" }),
    ).toEqual([]);
  });

  it("limits dashboard keyword matching to selected tag scope", () => {
    const taggedProfessors = [
      buildProfessor({
        id: 4,
        name: "高意愿导师",
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

  it("uses OR within one multi-select group", () => {
    expect(namesFor(professors, { universities: ["MIT", "Stanford"] })).toEqual([
      "Alice",
      "Bob",
      "Carol",
    ]);
  });

  it("uses AND across multi-select groups", () => {
    expect(
      namesFor(professors, {
        universities: ["MIT"],
        schools: ["AI Institute"],
        departments: ["Robotics"],
        titles: ["助理教授"],
        statuses: ["replied"],
      }),
    ).toEqual(["Carol"]);
  });

  it("filters by minimum match score and excludes unscored professors when threshold is set", () => {
    expect(namesFor(professors, { minMatchScore: "80" })).toEqual(["Alice"]);
  });

  it("filters nullable fields and unscored professors with the no-value options", () => {
    const sparselyProfiledProfessor = buildProfessor({
      id: 4,
      name: "Missing",
      title: null,
      university: null,
      school: null,
      department: null,
      match_score: null,
    });
    const completeProfessor = buildProfessor({
      id: 5,
      name: "Complete",
      title: "教授",
      university: "MIT",
      school: "School of Engineering",
      department: "EECS",
      match_score: 90,
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
    expect(
      namesFor(profs, { minMatchScore: NO_MATCH_SCORE_FILTER_VALUE }),
    ).toEqual(["Missing"]);
  });

  it("keeps unscored professors when minimum match score is empty", () => {
    expect(namesFor(professors, { minMatchScore: "" })).toEqual([
      "Alice",
      "Bob",
      "Carol",
    ]);
  });

  it("builds sorted non-empty options", () => {
    const options = buildDashboardFilterOptions([
      ...professors,
      buildProfessor({ id: 4, name: "Empty", university: "", school: null }),
    ]);

    expect(options.universities).toEqual(["MIT", "Stanford"]);
    expect(options.schools).toEqual([
      "AI Institute",
      "School of Engineering",
      "School of Medicine",
    ]);
    expect(options.departments).toEqual(["Bioengineering", "EECS", "Robotics"]);
    expect(options.titles).toEqual(["副教授", "教授", "助理教授"]);
  });

  it("limits school options to the selected universities", () => {
    const options = buildDashboardFilterOptions(professors, {
      ...createDefaultDashboardFilters(),
      universities: ["MIT"],
    });

    expect(options.schools).toEqual(["AI Institute", "School of Engineering"]);
  });

  it("limits department options to the selected universities and schools", () => {
    const mitOptions = buildDashboardFilterOptions(professors, {
      ...createDefaultDashboardFilters(),
      universities: ["MIT"],
    });

    expect(mitOptions.departments).toEqual(["EECS", "Robotics"]);

    const instituteOptions = buildDashboardFilterOptions(professors, {
      ...createDefaultDashboardFilters(),
      universities: ["MIT"],
      schools: ["AI Institute"],
    });

    expect(instituteOptions.departments).toEqual(["Robotics"]);
  });

  it("prunes filters when upstream organization selections change", () => {
    const pruned = pruneDashboardFilters(professors, {
      ...createDefaultDashboardFilters(),
      universities: ["MIT", "Unknown"],
      schools: ["AI Institute", "School of Medicine"],
        departments: ["EECS", "Unknown"],
        titles: ["教授", "不存在"],
        tagIds: ["404"],
      });

    expect(pruned.universities).toEqual(["MIT"]);
    expect(pruned.schools).toEqual(["AI Institute"]);
    expect(pruned.departments).toEqual([]);
    expect(pruned.titles).toEqual(["教授"]);
    expect(pruned.tagIds).toEqual([]);

    const schoolPruned = pruneDashboardFilters(professors, {
      ...createDefaultDashboardFilters(),
      universities: ["MIT"],
      schools: ["AI Institute"],
      departments: ["EECS", "Robotics"],
    });

    expect(schoolPruned.departments).toEqual(["Robotics"]);
  });

  it("keeps no-value selections while pruning dependent options", () => {
    const pruned = pruneDashboardFilters(
      [buildProfessor({ id: 4, name: "Missing" })],
      {
        ...createDefaultDashboardFilters(),
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

  it("matches selected options against trimmed dashboard fields", () => {
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

    const options = buildDashboardFilterOptions(professorsWithWhitespace, {
      universities: ["MIT"],
      schools: ["AI Institute"],
    });

    expect(options.schools).toEqual(["AI Institute"]);
    expect(options.departments).toEqual(["Robotics"]);
  });

  it("splits composite title options by explicit separators and keeps unsplit titles whole", () => {
    const professorsWithCompositeTitles = [
      buildProfessor({
        id: 4,
        name: "Professor Supervisor",
        title: "教授、博导",
      }),
      buildProfessor({
        id: 5,
        name: "Associate Supervisor",
        title: "副教授/硕导",
      }),
      buildProfessor({
        id: 6,
        name: "English Title",
        title: "Assistant Professor",
      }),
    ];

    const options = buildDashboardFilterOptions(professorsWithCompositeTitles);

    expect(options.titles).toEqual([
      "博导",
      "副教授",
      "教授",
      "硕导",
      "Assistant Professor",
    ]);
    expect(namesFor(professorsWithCompositeTitles, { titles: ["博导"] })).toEqual([
      "Professor Supervisor",
    ]);
    expect(
      namesFor(professorsWithCompositeTitles, {
        titles: ["Assistant Professor"],
      }),
    ).toEqual(["English Title"]);
    expect(namesFor(professorsWithCompositeTitles, { titles: ["Assistant"] })).toEqual(
      [],
    );
  });

  it("counts active advanced filters", () => {
    expect(
      getActiveDashboardFilterCount({
        ...createDefaultDashboardFilters(),
        universities: ["MIT"],
        titles: ["教授", "副教授"],
        minMatchScore: "80",
      }),
    ).toBe(3);
  });

  it("does not mutate the input array", () => {
    const input = [...professors];
    filterDashboardProfessors(input, {
      ...createDefaultDashboardFilters(),
      universities: ["MIT"],
    });

    expect(input.map((professor) => professor.name)).toEqual([
      "Alice",
      "Bob",
      "Carol",
    ]);
  });
});
