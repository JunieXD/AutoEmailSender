import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OtherSettingsCard } from "@/components/molecules/OtherSettingsCard";
import { NotificationProvider } from "@/context/NotificationContext";

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
    match_analysis_job_item_concurrency: 5,
    match_analysis_job_interval_seconds: 10,
    crawler_worker_count: 1,
    crawler_profile_enrichment_concurrency: 3,
    crawler_host_concurrency: 2,
    draft_max_tokens: 6000,
    batch_draft_generation_concurrency: 5,
    draft_rewrite_intensity: "moderate",
    draft_rewrite_tone: "polite",
    draft_rewrite_formality: "balanced",
    draft_rewrite_length: "default",
    draft_rewrite_specificity: "balanced",
    draft_template_preservation: "structure_first",
    draft_custom_instruction: "",
    intended_research_direction: "",
    updated_at: "2026-05-04T00:00:00Z",
  })),
  updateRuntimeSettings: vi.fn(async (payload) => ({
    ...payload,
    updated_at: "2026-05-04T00:00:01Z",
  })),
}));

const renderSettings = () =>
  render(
    <NotificationProvider>
      <OtherSettingsCard />
    </NotificationProvider>,
  );

describe("OtherSettingsCard", () => {
  beforeEach(() => {
    window.autoEmailSender = undefined;
    vi.clearAllMocks();
  });

  it("loads and saves runtime concurrency settings", async () => {
    const api = await import("@/lib/api/runtimeSettings");

    renderSettings();

    fireEvent.click(screen.getByRole("button", { name: /其他设置/ }));
    expect(await screen.findByLabelText("每个匹配任务同时分析导师数")).toHaveValue(5);
    expect(screen.getByLabelText("AI 草稿输出 token 上限")).toHaveValue(6000);
    expect(screen.getByLabelText("同时生成草稿数")).toHaveValue(5);
    expect(screen.getByLabelText("同时运行的抓取任务数")).toHaveValue(1);
    expect(screen.getByLabelText("同时补全导师详情页数")).toHaveValue(3);
    expect(
      screen.getByText(/智能抓取和导师管理页信息补全合计最多同时处理/),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("同一网站同时抓取页数")).toHaveValue(2);

    fireEvent.change(screen.getByLabelText("每个匹配任务同时分析导师数"), {
      target: { value: "4" },
    });
    fireEvent.change(screen.getByLabelText("AI 草稿输出 token 上限"), {
      target: { value: "4800" },
    });
    fireEvent.change(screen.getByLabelText("同时生成草稿数"), {
      target: { value: "6" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存全部设置" }));

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
      expect(screen.getByRole("button", { name: "保存全部设置" })).toBeEnabled();
    });
    expect(screen.getByLabelText("每个匹配任务同时分析导师数")).toHaveValue(4);
    expect(screen.getByLabelText("同时生成草稿数")).toHaveValue(6);
    expect(screen.getByTestId("notification-title")).toHaveTextContent("设置已保存");
    expect(screen.getByText("其他设置已更新。")).toBeInTheDocument();
    expect(screen.getByTestId("notification-card").parentElement).toHaveClass(
      "fixed",
      "right-4",
      "bottom-4",
    );
    expect(screen.getByRole("region", { name: "其他设置保存栏" })).not.toHaveTextContent(
      "设置已保存",
    );
  });

  it("shows a bottom-right error notification when saving fails", async () => {
    const api = await import("@/lib/api/runtimeSettings");
    vi.mocked(api.updateRuntimeSettings).mockRejectedValueOnce(new Error("服务暂不可用"));

    renderSettings();

    fireEvent.click(screen.getByRole("button", { name: /其他设置/ }));
    await screen.findByLabelText("每个匹配任务同时分析导师数");
    fireEvent.click(screen.getByRole("button", { name: "保存全部设置" }));

    expect(await screen.findByTestId("notification-title")).toHaveTextContent(
      "保存其他设置失败",
    );
    expect(screen.getByText("服务暂不可用")).toBeInTheDocument();
    expect(screen.getByTestId("notification-card").parentElement).toHaveClass(
      "fixed",
      "right-4",
      "bottom-4",
    );
  });

  it("falls back to the default batch draft concurrency when the setting is missing", async () => {
    const api = await import("@/lib/api/runtimeSettings");
    vi.mocked(api.getRuntimeSettings).mockResolvedValueOnce({
      match_analysis_job_worker_count: 1,
      match_analysis_job_item_concurrency: 5,
      match_analysis_job_interval_seconds: 10,
      crawler_worker_count: 1,
      crawler_profile_enrichment_concurrency: 3,
      crawler_host_concurrency: 2,
      draft_max_tokens: 6000,
      draft_rewrite_intensity: "moderate",
      draft_rewrite_tone: "polite",
      draft_rewrite_formality: "balanced",
      draft_rewrite_length: "default",
      draft_rewrite_specificity: "balanced",
      draft_template_preservation: "structure_first",
      draft_custom_instruction: "",
      intended_research_direction: "",
      updated_at: "2026-05-04T00:00:00Z",
    } as Awaited<ReturnType<typeof api.getRuntimeSettings>>);

    renderSettings();

    fireEvent.click(screen.getByRole("button", { name: /其他设置/ }));

    expect(await screen.findByLabelText("同时生成草稿数")).toHaveValue(5);
    expect(screen.getByRole("button", { name: /其他设置/ })).toHaveTextContent("草稿 5");
    expect(screen.getByRole("button", { name: /其他设置/ })).not.toHaveTextContent("undefined");
  });

  it("shows only the AI draft custom instruction in draft rewrite preferences", async () => {
    const api = await import("@/lib/api/runtimeSettings");

    renderSettings();

    fireEvent.click(screen.getByRole("button", { name: /其他设置/ }));
    const customInstruction = await screen.findByLabelText("AI 草稿补充要求");
    expect(screen.queryByRole("button", { name: "改写强度" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "语气" })).not.toBeInTheDocument();
    expect(screen.queryByText("示例效果")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "恢复草稿默认" })).not.toBeInTheDocument();

    fireEvent.change(customInstruction, {
      target: { value: "少用套话，结尾保持简短。" },
    });

    fireEvent.click(screen.getByRole("button", { name: "保存全部设置" }));
    await waitFor(() => {
      expect(api.updateRuntimeSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          draft_rewrite_intensity: "moderate",
          draft_rewrite_tone: "polite",
          draft_rewrite_formality: "balanced",
          draft_rewrite_length: "default",
          draft_rewrite_specificity: "balanced",
          draft_template_preservation: "structure_first",
          draft_custom_instruction: "少用套话，结尾保持简短。",
        }),
      );
    });
  });

  it("loads and saves the intended research direction", async () => {
    const api = await import("@/lib/api/runtimeSettings");

    renderSettings();

    fireEvent.click(screen.getByRole("button", { name: /其他设置/ }));
    const intendedDirection = await screen.findByLabelText("意向研究方向");
    expect(intendedDirection).toHaveValue("");

    fireEvent.change(intendedDirection, {
      target: { value: "医学自然语言处理、临床知识图谱" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存全部设置" }));

    await waitFor(() => {
      expect(api.updateRuntimeSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          intended_research_direction: "医学自然语言处理、临床知识图谱",
        }),
      );
    });
  });

  it("shows startup at login as unavailable outside the desktop app", async () => {
    renderSettings();

    fireEvent.click(screen.getByRole("button", { name: /其他设置/ }));

    expect(await screen.findByText("开机自启动")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /开机自启动/ })).toBeDisabled();
    expect(screen.getByText("仅安装后的 Windows 桌面版支持开机自启动。")).toBeInTheDocument();
  });

  it("keeps one global save action available in a sticky footer", async () => {
    renderSettings();

    fireEvent.click(screen.getByRole("button", { name: /其他设置/ }));

    const saveBar = await screen.findByRole("region", { name: "其他设置保存栏" });
    const saveButtons = screen.getAllByRole("button", { name: "保存全部设置" });
    const settingsContent = document.querySelector("#other-settings-card-content");
    const formContent = screen.getByTestId("other-settings-form-content");
    expect(saveButtons).toHaveLength(1);
    expect(saveBar).toHaveClass("sticky", "bottom-0");
    expect(saveBar).toContainElement(saveButtons[0]);
    expect(settingsContent).toHaveClass("other-settings-card-content");
    expect(settingsContent).toContainElement(saveBar);
    expect(formContent).toHaveClass("pb-6");
    expect(formContent).not.toHaveClass("pb-28");
    expect(saveBar.className).not.toContain("shadow-[");
    expect(screen.queryByRole("button", { name: "保存补充要求" })).not.toBeInTheDocument();
  });


  it("shows a desktop quit action when running in the desktop app", async () => {
    const quitApp = vi.fn(async () => undefined);
    window.autoEmailSender = buildDesktopApi({ quitApp });

    renderSettings();

    fireEvent.click(screen.getByRole("button", { name: /其他设置/ }));
    const quitButton = await screen.findByRole("button", { name: "退出桌面应用" });

    fireEvent.click(quitButton);

    await waitFor(() => {
      expect(quitApp).toHaveBeenCalledOnce();
    });
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

    renderSettings();

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
