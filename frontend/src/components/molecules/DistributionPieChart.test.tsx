import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DistributionPieChart } from "@/components/molecules/DistributionPieChart";
import { dashboardPieColors } from "@/lib/charting";

describe("DistributionPieChart", () => {
  const data = [
    { key: "a", label: "示例大学", count: 3 },
    { key: "b", label: "第二大学", count: 1 },
  ];

  it("renders legend values and percentages", () => {
    render(
      <DistributionPieChart
        title="学校分布"
        data={data}
        emptyText="暂无数据"
        legendLayout="columns"
        valueSuffix="位"
      />,
    );

    expect(screen.getByText("示例大学")).toBeInTheDocument();
    expect(screen.getByText("3 位")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("第二大学")).toBeInTheDocument();
    expect(screen.getByText("1 位")).toBeInTheDocument();
    expect(screen.getByText("25%")).toBeInTheDocument();
  });

  it("shows hover detail for a legend item", () => {
    render(
      <DistributionPieChart
        title="学校分布"
        data={data}
        emptyText="暂无数据"
        legendLayout="columns"
        valueSuffix="位"
      />,
    );

    fireEvent.mouseEnter(screen.getByText("示例大学"));

    expect(screen.getByRole("tooltip")).toHaveTextContent("示例大学");
    expect(screen.getByRole("tooltip")).toHaveTextContent("3 位");
    expect(screen.getByRole("tooltip")).toHaveTextContent("75%");
    expect(screen.getByRole("tooltip")).toHaveClass("pointer-events-none");
  });

  it("renders empty state when all counts are zero", () => {
    render(
      <DistributionPieChart
        title="学校分布"
        data={[{ key: "a", label: "示例大学", count: 0 }]}
        emptyText="暂无数据"
      />,
    );

    expect(screen.getByText("暂无数据")).toBeInTheDocument();
  });

  it("renders a full circle when one slice takes the whole pie", () => {
    render(
      <DistributionPieChart
        title="资料完整度概览"
        data={[{ key: "missing_research_direction", label: "缺研究方向", count: 1 }]}
        emptyText="暂无数据"
      />,
    );

    const fullSlice = screen.getByTestId("pie-full-slice-missing_research_direction");
    expect(fullSlice.tagName.toLowerCase()).toBe("circle");
    expect(fullSlice).toHaveAttribute("fill", dashboardPieColors[0]);
    expect(screen.getByText("缺研究方向")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
  });

  it("renders more than the old palette length without repeating the first color", () => {
    const manySlices = Array.from({ length: 12 }, (_, index) => ({
      key: `slice-${index}`,
      label: `分类 ${index + 1}`,
      count: 1,
    }));

    render(
      <DistributionPieChart
        title="分类分布"
        data={manySlices}
        emptyText="暂无数据"
        legendLayout="columns"
      />,
    );

    const firstSlice = screen.getByTestId("pie-slice-slice-0");
    const eleventhSlice = screen.getByTestId("pie-slice-slice-10");

    expect(firstSlice).toHaveAttribute("fill", dashboardPieColors[0]);
    expect(eleventhSlice).not.toHaveAttribute("fill", dashboardPieColors[0]);
  });

  it("uses three aligned legend columns for school distribution", () => {
    render(
      <DistributionPieChart
        title="学校分布"
        data={data}
        emptyText="暂无数据"
        legendLayout="columns"
        valueSuffix="位"
      />,
    );

    expect(screen.getByTestId("pie-legend-columns")).toBeInTheDocument();
    expect(screen.getByTestId("pie-legend-columns")).toHaveClass("max-h-64");
    expect(screen.getByTestId("pie-legend-columns")).toHaveClass("overflow-y-auto");
    expect(screen.getByTestId("pie-legend-row-a")).toHaveClass(
      "grid-cols-[minmax(0,1fr)_5rem_4rem]",
    );
    expect(screen.getByTestId("pie-legend-row-a")).toHaveTextContent("示例大学");
    expect(screen.getByTestId("pie-legend-row-a")).toHaveTextContent("3 位");
    expect(screen.getByTestId("pie-legend-row-a")).toHaveTextContent("75%");
  });

  it("keeps dense legends as one item per row while allowing horizontal scroll", () => {
    render(
      <DistributionPieChart
        title="资料完整度概览"
        data={[
          { key: "complete", label: "完整资料", count: 1 },
          { key: "missing_email", label: "缺邮箱", count: 1 },
          { key: "missing_research_direction", label: "缺研究方向", count: 1 },
          { key: "missing_recent_papers", label: "缺近期论文", count: 1 },
        ]}
        emptyText="暂无数据"
        legendLayout="horizontal-scroll"
      />,
    );

    const legend = screen.getByTestId("pie-legend-horizontal-scroll");
    expect(legend).toHaveClass("overflow-x-auto");
    expect(legend).not.toHaveClass("overflow-y-auto");
    expect(screen.getByTestId("pie-legend-scroll-row-complete")).toHaveClass("w-max");
    expect(screen.getByTestId("pie-legend-scroll-row-missing_research_direction")).toHaveTextContent("缺研究方向");
  });
});
