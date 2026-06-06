import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProfessorTagChips } from "./ProfessorTagChips";

const tag = (id: number, name: string) => ({
  id,
  name,
  text_color: "#166534",
  background_color: "#dcfce7",
});

describe("ProfessorTagChips", () => {
  it("shows no tag state", () => {
    render(<ProfessorTagChips tags={[]} />);

    expect(screen.getByText("暂无标签")).toBeInTheDocument();
  });

  it("limits visible tags and shows overflow count", () => {
    render(
      <ProfessorTagChips
        maxVisible={2}
        tags={[tag(1, "高意愿"), tag(2, "高强度"), tag(3, "羊导")]}
      />,
    );

    expect(screen.getByText("高意愿")).toBeInTheDocument();
    expect(screen.getByText("高强度")).toBeInTheDocument();
    expect(screen.queryByText("羊导")).not.toBeInTheDocument();
    expect(screen.getByText("+1")).toBeInTheDocument();
  });
});
