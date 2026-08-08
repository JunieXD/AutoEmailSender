import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DiagnosticLogPanel } from "@/components/organisms/DiagnosticLogPanel";
import {
  clearDiagnosticEvents,
  getDiagnosticEvents,
  recordDiagnosticEvent,
} from "@/lib/diagnostics";
import {
  exportCrawlerDebugLog,
  exportOperationLogs,
  listOperationLogs,
} from "@/lib/api/diagnosticsApi";
import { listCrawlJobs } from "@/lib/api/crawlJobsApi";

const notificationApi = vi.hoisted(() => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => notificationApi,
}));

vi.mock("@/lib/api/diagnosticsApi", () => ({
  exportCrawlerDebugLog: vi.fn(),
  exportOperationLogs: vi.fn(),
  listOperationLogs: vi.fn(),
}));

vi.mock("@/lib/api/crawlJobsApi", () => ({
  listCrawlJobs: vi.fn(),
}));

const backendLogs = [
  {
    id: 101,
    event_name: "crawl_job.create_failed",
    level: "error",
    category: "api",
    request_id: "req-101",
    message: "创建抓取任务失败，后端返回 500",
    created_at: "2026-04-25T10:00:00Z",
  },
  {
    id: 100,
    event_name: "profile.save",
    level: "info",
    category: "user_action",
    request_id: null,
    message: "保存配置成功",
    created_at: "2026-04-25T09:50:00Z",
  },
];

const crawlJobs = [
  {
    id: 42,
    university: "示例大学",
    school: "计算机学院",
    start_url: "https://example.edu/faculty",
    start_urls: ["https://example.edu/faculty"],
    entry_type: "list",
    llm_profile_id: null,
    status: "canceled",
    progress_current: 0,
    progress_total: 0,
    error_message: null,
    created_at: "2026-04-25T10:00:00Z",
    updated_at: "2026-04-25T10:30:00Z",
    page_count: 2,
    candidate_count: 5,
    latest_event_message: "任务已取消",
    input_tokens: 10,
    output_tokens: 5,
    cached_tokens: 3,
    total_tokens: 15,
    duration_seconds: 30,
  },
];

function seedLocalDiagnostics() {
  recordDiagnosticEvent({
    level: "error",
    category: "api",
    eventName: "api.request_failed",
    message: "创建抓取任务失败",
  });
  recordDiagnosticEvent({
    level: "info",
    category: "user_action",
    eventName: "profile.opened",
    message: "打开个人中心",
  });
}

function todayInputValue() {
  const date = new Date();
  const pad = (value: number) => String(value).padStart(2, "0");
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
  ].join("-");
}

function expectDateRangeParams(extra: Record<string, unknown> = {}) {
  return expect.objectContaining({
    start_at: expect.any(String),
    end_at: expect.any(String),
    ...extra,
  });
}

async function expandPanel() {
  fireEvent.click(screen.getByRole("button", { name: /开发诊断日志/ }));
  await waitFor(() => {
    expect(listOperationLogs).toHaveBeenCalled();
  });
}

function chooseSelectOption(label: string, optionName: string | RegExp) {
  fireEvent.click(screen.getByRole("button", { name: label }));
  fireEvent.click(screen.getByRole("option", { name: optionName }));
}

describe("DiagnosticLogPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearDiagnosticEvents();
    vi.mocked(listOperationLogs).mockResolvedValue({
      items: backendLogs,
      total: 2,
      limit: 20,
      offset: 0,
    });
    vi.mocked(exportOperationLogs).mockResolvedValue({
      exported_at: "2026-04-25T10:01:00Z",
      items: backendLogs,
      total: 2,
      filters: {},
    });
    vi.mocked(listCrawlJobs).mockResolvedValue(crawlJobs);
    vi.mocked(exportCrawlerDebugLog).mockResolvedValue(
      new Blob(['{"job_id":42}\n'], { type: "application/jsonl" }),
    );
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:diagnostics"),
      revokeObjectURL: vi.fn(),
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("默认折叠，不展示日志预览，也不主动加载后端日志", () => {
    seedLocalDiagnostics();

    render(<DiagnosticLogPanel />);

    expect(screen.getByRole("button", { name: /开发诊断日志/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.getByText(/本地\s*2\s*条/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "导出诊断日志" })).not.toBeInTheDocument();
    expect(listOperationLogs).not.toHaveBeenCalled();
  });

  it("点击后展开控制区并按默认今天加载后端日志", async () => {
    seedLocalDiagnostics();

    render(<DiagnosticLogPanel />);
    await expandPanel();

    expect(screen.getByRole("button", { name: /开发诊断日志/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByLabelText("导出日期")).toHaveValue(todayInputValue());
    expect(screen.getByRole("button", { name: "导出诊断日志" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "导出抓取日志" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "清空本地日志" })).toBeInTheDocument();
    expect(listOperationLogs).toHaveBeenCalledWith(
      expectDateRangeParams({ limit: 20 }),
    );
    expect(listCrawlJobs).toHaveBeenCalledWith({ limit: 50 });
  });

  it("诊断过滤下拉框在窄容器内可以收缩，避免菜单超出卡片", async () => {
    render(<DiagnosticLogPanel />);
    await expandPanel();

    const crawlJobSelect = screen.getByRole("button", { name: "智能抓取任务" });
    const crawlJobWrapper = crawlJobSelect.closest("label");
    const filterToolbar = crawlJobWrapper?.parentElement;

    expect(filterToolbar).toHaveClass("min-w-0");
    expect(crawlJobWrapper).toHaveClass("min-w-0");
    expect(crawlJobWrapper).toHaveClass("flex-1");
    expect(crawlJobWrapper).toHaveClass("max-w-full");
    expect(crawlJobSelect).toHaveClass("min-w-0");
  });

  it("把诊断日志导出和抓取任务日志导出拆成两个明确区域", async () => {
    render(<DiagnosticLogPanel />);
    await expandPanel();

    const diagnosticExportGroup = screen.getByRole("group", {
      name: "诊断日志导出",
    });
    expect(
      within(diagnosticExportGroup).getByRole("button", {
        name: "导出诊断日志",
      }),
    ).toBeInTheDocument();
    expect(within(diagnosticExportGroup).getByLabelText("导出日期")).toBeInTheDocument();
    expect(within(diagnosticExportGroup).getByRole("button", { name: "Level" })).toBeInTheDocument();
    expect(within(diagnosticExportGroup).getByRole("button", { name: "Category" })).toBeInTheDocument();
    expect(
      within(diagnosticExportGroup).queryByRole("button", {
        name: "导出抓取日志",
      }),
    ).not.toBeInTheDocument();

    const crawlerLogExportGroup = screen.getByRole("group", {
      name: "抓取任务日志",
    });
    expect(
      within(crawlerLogExportGroup).getByRole("button", {
        name: "智能抓取任务",
      }),
    ).toBeInTheDocument();
    expect(
      within(crawlerLogExportGroup).getByRole("button", {
        name: "导出抓取日志",
      }),
    ).toBeInTheDocument();
    expect(
      within(crawlerLogExportGroup).queryByRole("button", {
        name: "导出诊断日志",
      }),
    ).not.toBeInTheDocument();
  });

  it("诊断过滤下拉菜单向上浮起展开，避免被折叠卡片裁剪", async () => {
    render(<DiagnosticLogPanel />);
    await expandPanel();

    fireEvent.click(screen.getByRole("button", { name: "Category" }));

    const categoryMenu = screen.getByRole("listbox");
    expect(categoryMenu).toHaveClass("fixed");
    expect(categoryMenu).not.toHaveClass("relative");
    expect(categoryMenu).not.toHaveClass("absolute");
  });

  it("展开和收起诊断内容时使用过渡容器", async () => {
    render(<DiagnosticLogPanel />);
    await expandPanel();

    const content = document.getElementById("diagnostic-log-panel-content");
    expect(content).toHaveClass("collapsible-card-content");
    expect(content).toHaveAttribute("data-state", "open");

    fireEvent.click(screen.getByRole("button", { name: /开发诊断日志/ }));

    expect(content).toHaveAttribute("data-state", "closed");
    fireEvent.transitionEnd(content!, { propertyName: "grid-template-rows" });

    expect(document.getElementById("diagnostic-log-panel-content")).not.toBeInTheDocument();
  });

  it("后端加载失败时只显示不可用提示", async () => {
    vi.mocked(listOperationLogs).mockRejectedValue(new Error("backend down"));

    render(<DiagnosticLogPanel />);
    fireEvent.click(screen.getByRole("button", { name: /开发诊断日志/ }));

    expect(await screen.findByRole("button", { name: "导出诊断日志" })).toBeInTheDocument();
  });

  it("修改日期和筛选条件时会带上对应参数重新加载", async () => {
    render(<DiagnosticLogPanel />);
    await expandPanel();

    fireEvent.change(screen.getByLabelText("导出日期"), {
      target: { value: "2026-04-25" },
    });
    chooseSelectOption("Level", "warning（后端）");
    chooseSelectOption("Category", "crawler（后端）");

    await waitFor(() =>
      expect(listOperationLogs).toHaveBeenCalledWith(
        expectDateRangeParams({
          limit: 20,
          level: "warning",
          category: "crawler",
        }),
      ),
    );
  });

  it("点击导出会按选中日期生成合并 JSON 并触发下载", async () => {
    seedLocalDiagnostics();
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);

    render(<DiagnosticLogPanel />);
    await expandPanel();

    fireEvent.click(screen.getByRole("button", { name: "导出诊断日志" }));

    await waitFor(() => expect(clickSpy).toHaveBeenCalledTimes(1));
    const blob = vi.mocked(URL.createObjectURL).mock.calls[0][0] as Blob;
    const payload = JSON.parse(await blob.text());

    expect(exportOperationLogs).toHaveBeenCalledWith(expectDateRangeParams());
    expect(payload).toMatchObject({
      exportedAt: expect.any(String),
      selectedDate: todayInputValue(),
      frontend: {
        events: expect.arrayContaining([
          expect.objectContaining({ eventName: "api.request_failed" }),
          expect.objectContaining({ eventName: "profile.opened" }),
        ]),
      },
      backend: {
        items: expect.arrayContaining([
          expect.objectContaining({ event_name: "crawl_job.create_failed" }),
        ]),
      },
    });
    expect(notificationApi.notifySuccess).toHaveBeenCalledWith("诊断日志已导出");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:diagnostics");
    expect(getDiagnosticEvents()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          category: "user_action",
          eventName: "diagnostics.export_succeeded",
        }),
      ]),
    );
  });

  it("选择抓取任务后可以导出该任务 JSONL", async () => {
    render(<DiagnosticLogPanel />);
    await expandPanel();

    chooseSelectOption("智能抓取任务", /示例大学/);
    fireEvent.click(screen.getByRole("button", { name: "导出抓取日志" }));

    await waitFor(() => expect(exportCrawlerDebugLog).toHaveBeenCalledWith(42));
    expect(notificationApi.notifySuccess).toHaveBeenCalledWith("抓取日志已导出");
  });

  it("导出失败时会记录本地诊断事件", async () => {
    vi.mocked(URL.createObjectURL).mockImplementation(() => {
      throw new Error("blob failed");
    });

    render(<DiagnosticLogPanel />);
    await expandPanel();

    fireEvent.click(screen.getByRole("button", { name: "导出诊断日志" }));

    await waitFor(() => {
      expect(notificationApi.notifyError).toHaveBeenCalledWith(
        "导出诊断日志失败",
        "blob failed",
      );
    });
    expect(getDiagnosticEvents()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          category: "user_action",
          eventName: "diagnostics.export_failed",
          message: "blob failed",
        }),
      ]),
    );
  });

  it("清空本地日志会调用清理逻辑并更新数量", async () => {
    seedLocalDiagnostics();
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<DiagnosticLogPanel />);
    await expandPanel();

    expect(screen.getByText(/本地\s*2\s*条/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "清空本地日志" }));

    expect(getDiagnosticEvents()).toEqual([
      expect.objectContaining({
        category: "user_action",
        eventName: "diagnostics.local_logs_cleared",
      }),
    ]);
    expect(screen.getByText(/本地\s*1\s*条/)).toBeInTheDocument();
    expect(notificationApi.notifySuccess).toHaveBeenCalledWith("本地诊断日志已清空");
  });
});
