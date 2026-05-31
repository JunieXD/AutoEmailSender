import { MemoryRouter } from "react-router-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardPage } from "@/pages/DashboardPage";
import { resolveStatisticsSectionNavTop } from "@/lib/statisticsSectionNav";
import type { DashboardOverviewDTO } from "@/types";

const notifyError = vi.fn();
const lineChartRender = vi.fn();
const scrollIntoViewMock = vi.fn();
type SelectionState = {
  selectedIdentityId: number | null;
  selectedLlmProfileId: number | null;
  selectedIdentity: { id: number; name: string; profile_name: string } | null;
  selectedLlmProfile: { id: number; name: string } | null;
  loading: boolean;
};

let selectionState: SelectionState = {
  selectedIdentityId: 1,
  selectedLlmProfileId: 2,
  selectedIdentity: {
    id: 1,
    name: "博士申请邮箱",
    profile_name: "博士申请邮箱",
  },
  selectedLlmProfile: {
    id: 2,
    name: "OpenAI",
  },
  loading: false,
};

type MockIntersectionObserverEntry = Pick<
  IntersectionObserverEntry,
  "isIntersecting" | "target" | "intersectionRatio"
>;

let intersectionObserverCallback:
  | ((entries: IntersectionObserverEntry[], observer: IntersectionObserver) => void)
  | null = null;
let intersectionObserverOptions: IntersectionObserverInit | undefined;

class MockIntersectionObserver implements IntersectionObserver {
  readonly root = null;
  readonly rootMargin = "";
  readonly thresholds = [];

  constructor(callback: IntersectionObserverCallback, options?: IntersectionObserverInit) {
    intersectionObserverCallback = callback;
    intersectionObserverOptions = options;
  }

  disconnect() {}

  observe() {}

  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }

  unobserve() {}
}

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: () => selectionState,
}));

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => ({
    notifyError,
  }),
}));

const getDashboardOverview = vi.fn();

vi.mock("@/lib/api/dashboardApi", () => ({
  getDashboardOverview: (...args: unknown[]) => getDashboardOverview(...args),
}));

vi.mock("react-chartjs-2", () => ({
  Line: (props: unknown) => {
    lineChartRender(props);
    return <div data-testid="dashboard-email-line-chart" />;
  },
}));

vi.mock("chart.js", () => ({
  CategoryScale: {},
  Chart: { register: vi.fn() },
  Filler: {},
  Legend: {},
  LinearScale: {},
  LineElement: {},
  PointElement: {},
  Title: {},
  Tooltip: {},
}));

vi.mock("@/components/molecules/TokenVisualizationPanel", () => ({
  TokenVisualizationPanel: () => <section data-testid="token-visualization-panel">Token 消耗可视化</section>,
}));

const overview: DashboardOverviewDTO = {
  mentor: {
    summary: {
      total_professors: 3,
      matched_professors: 2,
      matched_rate: 0.666,
      high_match_professors: 2,
      high_score_uncontacted_count: 1,
      high_score_threshold: 85,
    },
    match_score_distribution: [
      { bucket: "unmatched", label: "未分析", count: 1 },
      { bucket: "0_59", label: "0-59", count: 0 },
      { bucket: "60_69", label: "60-69", count: 0 },
      { bucket: "70_79", label: "70-79", count: 0 },
      { bucket: "80_89", label: "80-89", count: 1 },
      { bucket: "90_100", label: "90-100", count: 1 },
    ],
    profile_completeness: [
      { key: "email", label: "有邮箱", count: 3, total: 3, rate: 1 },
      { key: "research_direction", label: "有研究方向", count: 2, total: 3, rate: 0.667 },
      { key: "recent_papers", label: "有近期论文", count: 2, total: 3, rate: 0.667 },
      { key: "profile_url", label: "有主页链接", count: 2, total: 3, rate: 0.667 },
      { key: "complete", label: "完整资料", count: 2, total: 3, rate: 0.667 },
    ],
    profile_completeness_distribution: [
      { key: "complete", label: "完整资料", count: 1, total: 3, rate: 0.333 },
      { key: "missing_email", label: "缺邮箱", count: 1, total: 3, rate: 0.333 },
      { key: "missing_research_direction", label: "缺研究方向", count: 1, total: 3, rate: 0.333 },
      { key: "missing_recent_papers", label: "缺近期论文", count: 0, total: 3, rate: 0 },
      { key: "missing_profile_url", label: "缺主页链接", count: 0, total: 3, rate: 0 },
      { key: "multiple_missing", label: "多项缺失", count: 0, total: 3, rate: 0 },
    ],
    school_distribution: [
      { school_name: "示例大学", count: 2 },
      { school_name: "第二大学", count: 1 },
    ],
    school_filters: [
      {
        university: "示例大学",
        count: 2,
        schools: [{ school_name: "计算机学院", count: 2 }],
      },
      {
        university: "第二大学",
        count: 1,
        schools: [{ school_name: "工程学院", count: 1 }],
      },
    ],
    active_filter: { university: null, school: null },
    high_score_uncontacted: [
      {
        professor_id: 10,
        name: "张老师",
        university: "示例大学",
        school: "计算机学院",
        department: null,
        match_score: 92,
        status: "matched",
        status_label: "待处理",
        reason: "高分但尚未触达",
        updated_at: "2026-05-22T00:00:00Z",
        missing_fields: [],
      },
    ],
    incomplete_professors: [
      {
        professor_id: 11,
        name: "王老师",
        university: "第二大学",
        school: "工程学院",
        department: null,
        match_score: 81,
        status: "preparing",
        status_label: "准备中",
        reason: "资料待补全",
        updated_at: "2026-05-22T00:00:00Z",
        missing_fields: ["邮箱"],
      },
    ],
  },
  email: {
    summary: {
      sent_count: 2,
      contacted_professor_count: 2,
      replied_count: 1,
      reply_rate: 0.5,
      send_failed_count: 1,
      send_failed_rate: 0.333,
      review_required_count: 1,
      scheduled_count: 1,
    },
    trend_30_days: [
      { date: "2026-05-21", label: "05/21", sent_count: 1, replied_count: 0, failed_count: 0 },
      { date: "2026-05-22", label: "05/22", sent_count: 1, replied_count: 1, failed_count: 1 },
    ],
    funnel: [
      { key: "matched", label: "已匹配", count: 3 },
      { key: "generating_draft", label: "草稿生成中", count: 2 },
      { key: "review_required", label: "待审核", count: 1 },
      { key: "approved", label: "已批准", count: 1 },
      { key: "scheduled", label: "已排程", count: 1 },
      { key: "sent", label: "已发送", count: 2 },
      { key: "replied", label: "已回复", count: 1 },
    ],
    status_distribution: [
      { status: "matched", label: "已匹配", count: 1 },
      { status: "scheduled", label: "已排程", count: 1 },
      { status: "sent", label: "已发送", count: 1 },
    ],
    follow_ups: [
      {
        professor_id: 12,
        task_id: 22,
        name: "李老师",
        university: "示例大学",
        school: "计算机学院",
        department: null,
        match_score: 90,
        status: "contacted",
        status_label: "已联系",
        reason: "已发送未回复",
        updated_at: "2026-05-22T00:00:00Z",
      },
    ],
  },
};

const legacyOverviewWithoutContactedProfessorCount = {
  ...overview,
  email: {
    ...overview.email,
    summary: {
      ...overview.email.summary,
      contacted_professor_count: undefined,
    },
  },
} as unknown as DashboardOverviewDTO;

const chooseNativeSelectOption = (label: string, optionName: string) => {
  fireEvent.click(screen.getByLabelText(label));
  fireEvent.click(screen.getByRole("option", { name: optionName }));
};

const emitIntersectionEntries = (entries: MockIntersectionObserverEntry[]) => {
  if (!intersectionObserverCallback) {
    throw new Error("IntersectionObserver callback was not registered");
  }

  intersectionObserverCallback(
    entries.map((entry) => ({
      boundingClientRect: {} as DOMRectReadOnly,
      intersectionRect: {} as DOMRectReadOnly,
      isIntersecting: entry.isIntersecting,
      intersectionRatio: entry.intersectionRatio,
      rootBounds: null,
      target: entry.target,
      time: 0,
    })),
    {} as IntersectionObserver,
  );
};

describe("DashboardPage", () => {
  beforeEach(() => {
    getDashboardOverview.mockReset();
    lineChartRender.mockReset();
    notifyError.mockReset();
    scrollIntoViewMock.mockReset();
    window.HTMLElement.prototype.scrollIntoView = scrollIntoViewMock;
    intersectionObserverCallback = null;
    intersectionObserverOptions = undefined;
    window.IntersectionObserver = MockIntersectionObserver;
    selectionState = {
      selectedIdentityId: 1,
      selectedLlmProfileId: 2,
      selectedIdentity: {
        id: 1,
        name: "博士申请邮箱",
        profile_name: "博士申请邮箱",
      },
      selectedLlmProfile: {
        id: 2,
        name: "OpenAI",
      },
      loading: false,
    };
    getDashboardOverview.mockResolvedValue(overview);
  });

  it("renders mentor and email dashboard sections", async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("统计面板")).toBeInTheDocument();
    expect(screen.queryByText("身份：博士申请邮箱")).not.toBeInTheDocument();
    expect(screen.queryByText("模型：OpenAI")).not.toBeInTheDocument();
    expect(await screen.findByText("导师概览")).toBeInTheDocument();
    expect(await screen.findByText("邮件触达")).toBeInTheDocument();
    expect(await screen.findByText("匹配分数分布")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "资料完整度概览" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "学校分布" })).toBeInTheDocument();
    expect(screen.getByTestId("mentor-overview-grid")).toHaveClass(
      "lg:grid-cols-[minmax(0,0.85fr)_minmax(0,0.85fr)_minmax(0,0.85fr)_minmax(22rem,1.45fr)]",
    );
    expect(screen.getByTestId("mentor-overview-grid")).toHaveClass("lg:items-stretch");
    expect(screen.getByTestId("mentor-overview-grid").getAttribute("style") ?? "").not.toContain("repeat(auto-fit");
    expect(screen.getByTestId("mentor-filter-bar")).toHaveClass("w-full");
    expect(screen.getByTestId("mentor-filter-bar")).toHaveClass("lg:col-span-3");
    expect(screen.getByTestId("mentor-profile-completeness-card")).toHaveClass("lg:row-span-2");
    expect(screen.getByTestId("mentor-profile-completeness-card")).toHaveClass("lg:col-start-4");
    expect(screen.getByTestId("mentor-profile-completeness-card")).toHaveClass("lg:row-start-1");
    expect(screen.getByTestId("mentor-profile-completeness-card")).toHaveClass("h-full");
    expect(screen.getByTestId("mentor-detail-grid").getAttribute("style") ?? "").toContain("repeat(auto-fit");
    expect(screen.getByTestId("mentor-detail-grid")).not.toHaveClass("lg:grid-cols-2");
    expect(screen.getByTestId("mentor-detail-grid")).toHaveClass("items-start");
    expect(screen.getByTestId("mentor-detail-grid")).toContainElement(
      screen.getByTestId("mentor-school-distribution-card"),
    );
    expect(screen.getByTestId("mentor-match-distribution-card")).toHaveClass("h-[22rem]");
    expect(screen.getByTestId("mentor-school-distribution-card")).toHaveClass("h-[22rem]");
    expect(screen.getByTestId("mentor-match-distribution-card")).toContainElement(
      screen.getByTestId("match-distribution-plot"),
    );
    expect(screen.getByTestId("match-distribution-plot")).toHaveClass("h-40");
    expect(screen.getByTestId("match-distribution-chart-window")).not.toHaveClass("overflow-x-auto");
    expect(screen.getByTestId("match-distribution-chart-body")).toHaveClass("w-full");
    expect(screen.getByTestId("match-distribution-chart-body")).not.toHaveClass("min-w-[520px]");
    expect(screen.getByTestId("mentor-profile-completeness-card")).toContainElement(
      screen.getByTestId("pie-legend-horizontal-scroll"),
    );
    expect(screen.getByTestId("mentor-school-distribution-card")).toContainElement(
      screen.getByTestId("pie-legend-columns"),
    );
    expect(screen.getByTestId("mentor-school-distribution-card")).toHaveTextContent("示例大学");
    expect(screen.getByTestId("mentor-school-distribution-card")).toHaveTextContent("2 位");
    expect(screen.getByTestId("mentor-school-distribution-card")).toHaveTextContent("67%");
    expect(screen.getByLabelText("学校筛选")).toBeInTheDocument();
    expect(screen.getByLabelText("学院筛选")).toBeInTheDocument();
    const mentorFilterBar = screen.getByTestId("mentor-filter-bar");
    expect(within(mentorFilterBar).getByLabelText("学院筛选")).toBeDisabled();
    expect(within(mentorFilterBar).getByText("请先选择学校")).toBeInTheDocument();
    expect(mentorFilterBar.querySelector("select")).toBeNull();
    expect(screen.getByTestId("mentor-overview-grid")).toContainElement(screen.getByTestId("mentor-filter-bar"));
    expect(
      screen.getByTestId("mentor-filter-bar").compareDocumentPosition(screen.getByText("导师总数")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(await screen.findByText("发送趋势")).toBeInTheDocument();
    expect(screen.getByTestId("email-outreach-filters")).toBeInTheDocument();
    expect(screen.getByLabelText("邮件触达时间筛选")).toBeInTheDocument();
    expect(screen.getByLabelText("邮件触达学校筛选")).toBeInTheDocument();
    expect(screen.getByLabelText("邮件触达学院筛选")).toBeInTheDocument();
    expect(screen.getByTestId("email-outreach-filters").querySelector("select")).toBeNull();
    expect(screen.getByTestId("email-outreach-filters")).not.toHaveTextContent("刷新统计");
    expect(screen.getByTestId("email-metrics-grid").getAttribute("style") ?? "").toContain("repeat(auto-fit");
    expect(screen.getByTestId("email-metrics-grid")).not.toHaveClass("md:grid-cols-3");
    expect(screen.getByTestId("email-metrics-grid")).not.toHaveClass("md:grid-cols-2");
    expect(screen.getByTestId("email-trend-grid")).toHaveClass("grid-cols-1");
    expect(screen.getByTestId("email-trend-grid")).not.toHaveClass("xl:grid-cols-2");
    expect(screen.getByTestId("email-trend-card")).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-email-line-chart")).toBeInTheDocument();
    expect(screen.queryByText("全部导师")).not.toBeInTheDocument();
    expect(screen.queryByText("发送 / 回复")).not.toBeInTheDocument();
    expect(screen.queryByText("近 30 天发送 / 回复 / 失败趋势")).not.toBeInTheDocument();
    expect(screen.queryByText("失败")).not.toBeInTheDocument();
    expect(screen.queryByText("高分未联系")).not.toBeInTheDocument();
    expect(screen.queryByText("高分未联系导师")).not.toBeInTheDocument();
    expect(screen.queryByText("待补全导师")).not.toBeInTheDocument();
    expect(screen.queryByText("发送失败")).not.toBeInTheDocument();
    expect(screen.queryByText("待审核草稿")).not.toBeInTheDocument();
    expect(screen.queryByText("已排程邮件")).not.toBeInTheDocument();
    expect(screen.queryByText("邮件状态漏斗")).not.toBeInTheDocument();
    expect(screen.queryByText("邮件任务状态分布")).not.toBeInTheDocument();
    expect(screen.queryByText("高价值待跟进邮件")).not.toBeInTheDocument();
    expect(screen.getByTestId("token-visualization-panel")).toBeInTheDocument();
    expect(screen.getByText("Token 消耗可视化")).toBeInTheDocument();
    expect(screen.getByTestId("statistics-section-nav")).toBeInTheDocument();
    expect(screen.getByTestId("statistics-sections-shell")).toHaveClass("lg:pl-24");
    expect(screen.getByTestId("statistics-sections-shell")).toHaveClass("xl:pl-24");
    expect(screen.getByTestId("statistics-section-nav")).toHaveClass("lg:left-[max(-0.5rem,calc((100vw-80rem)/2-0.5rem))]");
    expect(screen.getByRole("button", { name: "导师" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "邮件" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Token" })).toBeInTheDocument();
    expect(screen.getByTestId("statistics-section-mentor")).toHaveClass("scroll-mt-44");
    expect(screen.getByTestId("statistics-section-email")).toHaveClass("scroll-mt-44");
    expect(screen.getByTestId("statistics-section-token")).toHaveClass("scroll-mt-44");
  });

  it("renders email sending trend as an index-hover line chart", async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId("dashboard-email-line-chart")).toBeInTheDocument();

    const props = lineChartRender.mock.calls.at(-1)?.[0] as {
      data: { labels: string[]; datasets: Array<{ label: string; data: number[]; fill: boolean; tension: number }> };
      options: {
        responsive: boolean;
        maintainAspectRatio: boolean;
        interaction: { intersect: boolean; mode: string };
        plugins: {
          tooltip: {
            callbacks: {
              label: (context: { dataset: { label: string }; raw: number }) => string;
              footer: (items: Array<{ dataIndex: number }>) => string;
            };
          };
        };
        scales: { x: { ticks: { autoSkip: boolean; maxTicksLimit: number } } };
      };
    };

    expect(props.data.labels).toEqual(["05/21", "05/22"]);
    expect(props.data.datasets.map((dataset) => dataset.label)).toEqual(["发送", "回复"]);
    expect(props.data.datasets[0].data).toEqual([1, 1]);
    expect(props.data.datasets[1].data).toEqual([0, 1]);
    expect(props.data.datasets.every((dataset) => dataset.fill)).toBe(true);
    expect(props.data.datasets.every((dataset) => dataset.tension === 0.32)).toBe(true);
    expect(props.options.responsive).toBe(true);
    expect(props.options.maintainAspectRatio).toBe(false);
    expect(props.options.interaction).toEqual({ intersect: false, mode: "index" });
    expect(props.options.scales.x.ticks.autoSkip).toBe(true);
    expect(props.options.scales.x.ticks.maxTicksLimit).toBe(2);
    expect(props.options.plugins.tooltip.callbacks.label({
      dataset: { label: "发送" },
      raw: 2,
    })).toBe("发送: 2 封");
    expect(props.options.plugins.tooltip.callbacks.footer([{ dataIndex: 1 }])).toContain("合计 2 封");
  });

  it("keeps every email trend day while adapting crowded x-axis labels", async () => {
    getDashboardOverview.mockResolvedValue({
      ...overview,
      email: {
        ...overview.email,
        trend_30_days: Array.from({ length: 30 }, (_, index) => ({
          date: `2026-05-${String(index + 1).padStart(2, "0")}`,
          label: `05/${String(index + 1).padStart(2, "0")}`,
          sent_count: index === 0 || index === 29 ? 1 : 0,
          replied_count: index === 10 ? 1 : 0,
          failed_count: 0,
        })),
      },
    });

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId("dashboard-email-line-chart")).toBeInTheDocument();

    const props = lineChartRender.mock.calls.at(-1)?.[0] as {
      data: { labels: string[]; datasets: Array<{ data: number[] }> };
      options: {
        scales: { x: { ticks: { autoSkip: boolean; maxRotation: number; maxTicksLimit: number } } };
        plugins: { tooltip: { callbacks: { footer: (items: Array<{ dataIndex: number }>) => string } } };
      };
    };

    expect(props.data.labels).toHaveLength(30);
    expect(props.data.labels[0]).toBe("05/01");
    expect(props.data.labels[29]).toBe("05/30");
    expect(props.data.datasets[0].data).toHaveLength(30);
    expect(props.data.datasets[0].data[0]).toBe(1);
    expect(props.data.datasets[0].data[29]).toBe(1);
    expect(props.options.scales.x.ticks.autoSkip).toBe(true);
    expect(props.options.scales.x.ticks.maxRotation).toBe(35);
    expect(props.options.scales.x.ticks.maxTicksLimit).toBe(10);
    expect(props.options.plugins.tooltip.callbacks.footer([{ dataIndex: 29 }])).toContain("合计 1 封");
  });

  it("shows token-style hover details for match score distribution", async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    );

    const interactionLayer = await screen.findByTestId("match-distribution-interaction-layer");
    vi.spyOn(interactionLayer, "getBoundingClientRect").mockReturnValue({
      x: 100,
      y: 100,
      width: 600,
      height: 160,
      top: 100,
      right: 700,
      bottom: 260,
      left: 100,
      toJSON: () => ({}),
    });
    fireEvent.mouseMove(interactionLayer, { clientX: 660, clientY: 180 });

    expect(screen.getByRole("tooltip")).toHaveTextContent("90-100");
    expect(screen.getByRole("tooltip")).toHaveTextContent("导师数");
    expect(screen.getByRole("tooltip")).toHaveTextContent("1 位");
    expect(screen.getByRole("tooltip")).toHaveTextContent("33%");
  });

  it("reloads mentor analysis when school and college filters change", async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    );

    await screen.findByText("导师概览");
    chooseNativeSelectOption("学校筛选", "示例大学（2）");

    await waitFor(() => {
      expect(getDashboardOverview).toHaveBeenLastCalledWith({
        identityId: 1,
        llmProfileId: 2,
        university: "示例大学",
        school: null,
        emailUniversity: null,
        emailSchool: null,
        startDate: null,
        endDate: null,
      });
    });

    chooseNativeSelectOption("学院筛选", "计算机学院（2）");

    await waitFor(() => {
      expect(getDashboardOverview).toHaveBeenLastCalledWith({
        identityId: 1,
        llmProfileId: 2,
        university: "示例大学",
        school: "计算机学院",
        emailUniversity: null,
        emailSchool: null,
        startDate: null,
        endDate: null,
      });
    });
  });

  it("smoothly scrolls to each statistics section from the section nav", async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    );

    await screen.findByText("Token 消耗可视化");

    fireEvent.click(screen.getByRole("button", { name: "邮件" }));

    expect(scrollIntoViewMock).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });

    fireEvent.click(screen.getByRole("button", { name: "Token" }));

    expect(scrollIntoViewMock).toHaveBeenCalledTimes(2);
    expect(scrollIntoViewMock).toHaveBeenLastCalledWith({ behavior: "smooth", block: "start" });
  });

  it("updates the active nav item when the visible statistics section changes", async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    );

    const mentorSection = await screen.findByTestId("statistics-section-mentor");
    const emailSection = screen.getByTestId("statistics-section-email");
    const tokenSection = screen.getByTestId("statistics-section-token");

    expect(screen.getByRole("button", { name: "导师" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: "邮件" })).toHaveAttribute("aria-current", "false");

    emitIntersectionEntries([
      { target: emailSection, isIntersecting: true, intersectionRatio: 0.8 },
      { target: mentorSection, isIntersecting: false, intersectionRatio: 0.2 },
    ]);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "邮件" })).toHaveAttribute("aria-current", "true");
    });

    emitIntersectionEntries([
      { target: tokenSection, isIntersecting: true, intersectionRatio: 0.85 },
      { target: emailSection, isIntersecting: false, intersectionRatio: 0.1 },
    ]);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Token" })).toHaveAttribute("aria-current", "true");
    });
  });

  it("observes small visibility ratios so the bottom token section can become active", async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    );

    await screen.findByTestId("statistics-section-token");

    expect(intersectionObserverOptions?.threshold).toEqual(expect.arrayContaining([0.05, 0.1, 0.2]));
  });

  it("keeps the section nav centered after the summary card scrolls away", () => {
    expect(resolveStatisticsSectionNavTop({ headerBottom: 120, summaryCardBottom: 248, rootFontSize: 16 })).toBe(304);
    expect(resolveStatisticsSectionNavTop({ headerBottom: 320, summaryCardBottom: 40, rootFontSize: 16 })).toBe(344);
    expect(resolveStatisticsSectionNavTop({ headerBottom: 0, summaryCardBottom: 40, rootFontSize: 16 })).toBe(160);
  });

  it("reloads email outreach metrics when email filters change", async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    );

    await screen.findByText("邮件触达");
    chooseNativeSelectOption("邮件触达学校筛选", "示例大学（2）");

    await waitFor(() => {
      expect(getDashboardOverview).toHaveBeenLastCalledWith({
        identityId: 1,
        llmProfileId: 2,
        university: null,
        school: null,
        emailUniversity: "示例大学",
        emailSchool: null,
        startDate: null,
        endDate: null,
      });
    });

    chooseNativeSelectOption("邮件触达学院筛选", "计算机学院（2）");

    await waitFor(() => {
      expect(getDashboardOverview).toHaveBeenLastCalledWith(
        expect.objectContaining({
          emailUniversity: "示例大学",
          emailSchool: "计算机学院",
        }),
      );
    });

    chooseNativeSelectOption("邮件触达时间筛选", "最近 30 天");

    await waitFor(() => {
      expect(getDashboardOverview).toHaveBeenLastCalledWith(
        expect.objectContaining({
          startDate: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
          endDate: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
        }),
      );
    });
  });

  it("uses all email outreach data by default", async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    );

    await screen.findByText("邮件触达");

    await waitFor(() => {
      expect(getDashboardOverview).toHaveBeenCalledWith({
        identityId: 1,
        llmProfileId: 2,
        university: null,
        school: null,
        emailUniversity: null,
        emailSchool: null,
        startDate: null,
        endDate: null,
      });
    });
  });

  it("does not show NaN when contacted professor count is missing", async () => {
    getDashboardOverview.mockResolvedValue(legacyOverviewWithoutContactedProfessorCount);

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    );

    await screen.findByText("邮件触达");

    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
    expect(screen.getByText("1 / 2 位导师")).toBeInTheDocument();
  });

  it("shows a setup prompt when identity or model is missing", async () => {
    selectionState = {
      selectedIdentityId: null,
      selectedLlmProfileId: null,
      selectedIdentity: null,
      selectedLlmProfile: null,
      loading: false,
    };

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("请先选择身份和模型。")).toBeInTheDocument();
  });
});
