import { describe, expect, it } from "vitest";
import {
  assignPieSliceColors,
  arePieSliceColorsSimilar,
  dashboardPieColors,
} from "@/lib/charting";

describe("pie chart colors", () => {
  it("provides a 36 color palette without black or white", () => {
    expect(dashboardPieColors).toHaveLength(36);
    expect(new Set(dashboardPieColors).size).toBe(36);
    expect(dashboardPieColors).not.toContain("#000000");
    expect(dashboardPieColors).not.toContain("#ffffff");
    expect(dashboardPieColors).not.toContain("#FFFFFF");
  });

  it("keeps adjacent generated colors visually distinct", () => {
    const colors = assignPieSliceColors(36);

    colors.forEach((color, index) => {
      const next = colors[(index + 1) % colors.length];
      expect(color).not.toBe(next);
      expect(arePieSliceColorsSimilar(color, next)).toBe(false);
    });
  });

  it("keeps the first and last generated colors distinct", () => {
    const colors = assignPieSliceColors(12);

    expect(colors[0]).not.toBe(colors[colors.length - 1]);
    expect(arePieSliceColorsSimilar(colors[0], colors[colors.length - 1])).toBe(false);
  });

  it("still avoids adjacent duplicates when more slices than palette colors are needed", () => {
    const colors = assignPieSliceColors(43);

    expect(colors).toHaveLength(43);
    colors.forEach((color, index) => {
      const next = colors[(index + 1) % colors.length];
      expect(color).not.toBe(next);
      expect(arePieSliceColorsSimilar(color, next)).toBe(false);
    });
  });
});
