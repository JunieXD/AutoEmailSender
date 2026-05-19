import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OtherSettingsCard } from "@/components/molecules/OtherSettingsCard";

vi.mock("@/lib/api/runtimeSettings", () => ({
  defaultDraftRewritePreferences: {
    draft_rewrite_intensity: "moderate",
    draft_rewrite_tone: "polite",
    draft_rewrite_formality: "balanced",
    draft_rewrite_length: "default",
    draft_rewrite_specificity: "balanced",
    draft_template_preservation: "structure_first",
  },
  getRuntimeSettings: vi.fn(async () => ({
    match_analysis_job_worker_count: 1,
    match_analysis_job_item_concurrency: 3,
    match_analysis_job_interval_seconds: 10,
    crawler_worker_count: 2,
    crawler_profile_enrichment_concurrency: 3,
    crawler_host_concurrency: 1,
    draft_max_tokens: 6000,
    batch_draft_generation_concurrency: 3,
    draft_rewrite_intensity: "moderate",
    draft_rewrite_tone: "polite",
    draft_rewrite_formality: "balanced",
    draft_rewrite_length: "default",
    draft_rewrite_specificity: "balanced",
    draft_template_preservation: "structure_first",
    updated_at: "2026-05-04T00:00:00Z",
  })),
  updateRuntimeSettings: vi.fn(async (payload) => ({
    ...payload,
    updated_at: "2026-05-04T00:00:01Z",
  })),
}));

describe("OtherSettingsCard", () => {
  beforeEach(() => {
    window.autoEmailSender = undefined;
    vi.clearAllMocks();
  });

  const chooseSelectOption = (label: string, optionName: string) => {
    fireEvent.click(screen.getByRole("button", { name: label }));
    fireEvent.click(screen.getByRole("option", { name: optionName }));
  };

  it("loads and saves runtime concurrency settings", async () => {
    const api = await import("@/lib/api/runtimeSettings");

    render(<OtherSettingsCard />);

    fireEvent.click(screen.getByRole("button", { name: /其他设置/ }));
    expect(await screen.findByLabelText("批量匹配分析并发数")).toHaveValue(3);
    expect(screen.getByLabelText("AI 草稿输出 token 上限")).toHaveValue(6000);
    expect(screen.getByLabelText("批量邮件 LLM 草稿并发数")).toHaveValue(3);

    fireEvent.change(screen.getByLabelText("批量匹配分析并发数"), {
      target: { value: "4" },
    });
    fireEvent.change(screen.getByLabelText("AI 草稿输出 token 上限"), {
      target: { value: "4800" },
    });
    fireEvent.change(screen.getByLabelText("批量邮件 LLM 草稿并发数"), {
      target: { value: "6" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() => {
      expect(api.updateRuntimeSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          match_analysis_job_item_concurrency: 4,
          draft_max_tokens: 4800,
          batch_draft_generation_concurrency: 6,
        }),
      );
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "保存设置" })).toBeEnabled();
    });
    expect(screen.getByLabelText("批量匹配分析并发数")).toHaveValue(4);
    expect(screen.getByLabelText("批量邮件 LLM 草稿并发数")).toHaveValue(6);
  });

  it("falls back to the default batch draft concurrency when the setting is missing", async () => {
    const api = await import("@/lib/api/runtimeSettings");
    vi.mocked(api.getRuntimeSettings).mockResolvedValueOnce({
      match_analysis_job_worker_count: 1,
      match_analysis_job_item_concurrency: 3,
      match_analysis_job_interval_seconds: 10,
      crawler_worker_count: 2,
      crawler_profile_enrichment_concurrency: 3,
      crawler_host_concurrency: 1,
      draft_max_tokens: 6000,
      draft_rewrite_intensity: "moderate",
      draft_rewrite_tone: "polite",
      draft_rewrite_formality: "balanced",
      draft_rewrite_length: "default",
      draft_rewrite_specificity: "balanced",
      draft_template_preservation: "structure_first",
      updated_at: "2026-05-04T00:00:00Z",
    } as Awaited<ReturnType<typeof api.getRuntimeSettings>>);

    render(<OtherSettingsCard />);

    fireEvent.click(screen.getByRole("button", { name: /其他设置/ }));

    expect(await screen.findByLabelText("批量邮件 LLM 草稿并发数")).toHaveValue(3);
    expect(screen.getByRole("button", { name: /其他设置/ })).toHaveTextContent("草稿并发 3");
    expect(screen.getByRole("button", { name: /其他设置/ })).not.toHaveTextContent("undefined");
  });

  it("loads saves and resets draft rewrite preferences", async () => {
    const api = await import("@/lib/api/runtimeSettings");

    render(<OtherSettingsCard />);

    fireEvent.click(screen.getByRole("button", { name: /其他设置/ }));
    expect(await screen.findByRole("button", { name: "改写强度" })).toHaveTextContent("中等");
    expect(screen.getByText("示例效果")).toBeInTheDocument();

    chooseSelectOption("改写强度", "明显");
    chooseSelectOption("语气", "专业");
    expect(screen.getAllByText(/更主动/).length).toBeGreaterThan(0);
    expect(
      screen.getByText(
        (content, element) =>
          element?.tagName.toLowerCase() === "p" &&
          content.includes("老师您好，基于我对您课题组人工智能研究方向的了解"),
      ),
    ).not.toHaveTextContent(/重写|调整|保留原模板表达/);

    fireEvent.click(screen.getByRole("button", { name: "恢复草稿默认" }));
    expect(screen.getByRole("button", { name: "改写强度" })).toHaveTextContent("中等");

    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    await waitFor(() => {
      expect(api.updateRuntimeSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          draft_rewrite_intensity: "moderate",
          draft_rewrite_tone: "polite",
          draft_rewrite_formality: "balanced",
          draft_rewrite_length: "default",
          draft_rewrite_specificity: "balanced",
          draft_template_preservation: "structure_first",
        }),
      );
    });
  });

  it("updates the local preview for every draft rewrite preference", async () => {
    render(<OtherSettingsCard />);

    fireEvent.click(screen.getByRole("button", { name: /其他设置/ }));
    await screen.findByRole("button", { name: "改写强度" });

    chooseSelectOption("改写强度", "轻微");
    expect(screen.getAllByText(/轻微调整/).length).toBeGreaterThan(0);

    chooseSelectOption("语气", "亲和");
    expect(screen.getAllByText(/表达更亲近/).length).toBeGreaterThan(0);

    chooseSelectOption("正式程度", "更正式");
    expect(screen.getAllByText(/正式学术邮件/).length).toBeGreaterThan(0);

    chooseSelectOption("长度", "更详细");
    expect(screen.getAllByText(/增加背景和期待/).length).toBeGreaterThan(0);

    chooseSelectOption("具体性", "细节更足");
    expect(screen.getAllByText(/点出研究交集/).length).toBeGreaterThan(0);

    chooseSelectOption("模板保留度", "更重内容表达");
    expect(screen.getAllByText(/优先重组内容/).length).toBeGreaterThan(0);
  });

  it("shows startup at login as unavailable outside the desktop app", async () => {
    render(<OtherSettingsCard />);

    fireEvent.click(screen.getByRole("button", { name: /其他设置/ }));

    expect(await screen.findByText("开机自启动")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /开机自启动/ })).toBeDisabled();
    expect(screen.getByText("仅安装后的 Windows 桌面版支持开机自启动。")).toBeInTheDocument();
  });

  it("loads and updates startup at login in the desktop app", async () => {
    const setStartupAtLoginEnabled = vi.fn(async (enabled: boolean) => ({
      supported: true,
      enabled,
    }));
    window.autoEmailSender = buildDesktopApi({
      getStartupAtLoginStatus: vi.fn(async () => ({ supported: true, enabled: true })),
      setStartupAtLoginEnabled,
    });

    render(<OtherSettingsCard />);

    fireEvent.click(screen.getByRole("button", { name: /其他设置/ }));
    const startupCheckbox = await screen.findByRole("checkbox", { name: /开机自启动/ });
    await waitFor(() => expect(startupCheckbox).toBeChecked());

    fireEvent.click(startupCheckbox);

    await waitFor(() => {
      expect(setStartupAtLoginEnabled).toHaveBeenCalledWith(false);
    });
    await waitFor(() => expect(startupCheckbox).not.toBeChecked());
  });
});

function buildDesktopApi(overrides: Partial<NonNullable<typeof window.autoEmailSender>> = {}) {
  return {
    backendBaseUrl: "http://127.0.0.1:48123",
    getVersion: async () => "0.1.0",
    checkForUpdate: async () => ({ state: "idle", version: "0.1.0" }) as const,
    downloadUpdate: async () => ({ state: "idle", version: "0.1.0" }) as const,
    switchToFullDownload: async () => ({ state: "idle", version: "0.1.0" }) as const,
    quitAndInstall: async () => undefined,
    onUpdateStatus: () => () => undefined,
    ...overrides,
  };
}
