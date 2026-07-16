import { describe, expect, it } from "vitest";
import {
  EMPTY_PROFESSOR_FIELD_VALUE,
  formatProfessorSearchField,
  isProfessorSearchFieldEmpty,
  matchesProfessorSearchField,
  normalizeProfessorSearchText,
} from "./professorSearchField";

describe("professorSearchField", () => {
  it("normalizes nullable search text", () => {
    expect(normalizeProfessorSearchText(null)).toBe("");
    expect(normalizeProfessorSearchText("  AI Systems  ")).toBe("ai systems");
  });

  it("treats null, empty strings, and whitespace as empty", () => {
    expect(isProfessorSearchFieldEmpty(null)).toBe(true);
    expect(isProfessorSearchFieldEmpty(undefined)).toBe(true);
    expect(isProfessorSearchFieldEmpty("")).toBe(true);
    expect(isProfessorSearchFieldEmpty(" \t ")).toBe(true);
    expect(isProfessorSearchFieldEmpty("AI")).toBe(false);
  });

  it("uses 无 only as the empty-field query", () => {
    expect(matchesProfessorSearchField(null, EMPTY_PROFESSOR_FIELD_VALUE)).toBe(true);
    expect(matchesProfessorSearchField("   ", EMPTY_PROFESSOR_FIELD_VALUE)).toBe(true);
    expect(matchesProfessorSearchField("无人机系统", EMPTY_PROFESSOR_FIELD_VALUE)).toBe(false);
    expect(matchesProfessorSearchField("无", EMPTY_PROFESSOR_FIELD_VALUE)).toBe(false);
    expect(matchesProfessorSearchField("无人机系统", normalizeProfessorSearchText(" 无人机 "))).toBe(true);
  });

  it("formats only empty display values as 无", () => {
    expect(formatProfessorSearchField(null)).toBe("无");
    expect(formatProfessorSearchField(" \t ")).toBe("无");
    expect(formatProfessorSearchField("  AI Systems  ")).toBe("AI Systems");
  });
});
