import { expect, it } from "vitest";
import { dueTone } from "./todos";

// Local times, so the result does not depend on the machine's time zone.
const now = new Date(2026, 9, 26, 10, 0).getTime();
const due = (dueDate: string | null, dueTime: string | null = null) => ({ dueDate, dueTime });

it("marks open todos past due red, and those due today or tomorrow yellow", () => {
  expect(dueTone(due("2026-10-25"), false, now)).toBe("text-destructive");
  expect(dueTone(due("2026-10-26", "09:30"), false, now)).toBe("text-destructive");
  expect(dueTone(due("2026-10-26", "18:00"), false, now)).toBe("text-warning");
  // A date without a time lasts the whole day.
  expect(dueTone(due("2026-10-26"), false, now)).toBe("text-warning");
  expect(dueTone(due("2026-10-27"), false, now)).toBe("text-warning");
  expect(dueTone(due("2026-10-28"), false, now)).toBe("text-muted-foreground");
});

it("leaves done todos and those without a due date uncoloured", () => {
  expect(dueTone(due("2026-10-25"), true, now)).toBe("text-muted-foreground");
  expect(dueTone(due(null), false, now)).toBe("text-muted-foreground");
});
