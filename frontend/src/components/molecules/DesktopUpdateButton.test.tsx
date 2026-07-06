import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DesktopUpdateButton } from "@/components/molecules/DesktopUpdateButton";
import type { DesktopUpdateStatus } from "@/types/desktop";

const notifySuccess = vi.fn();
const notifyError = vi.fn();
const confirm = vi.fn();

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => ({
    notifySuccess,
    notifyError,
  }),
}));

vi.mock("@/lib/useConfirmDialog", () => ({
  useConfirmDialog: () => ({
    confirm,
    dialog: null,
  }),
}));

describe("DesktopUpdateButton", () => {
  beforeEach(() => {
    Reflect.deleteProperty(window, "autoEmailSender");
    window.localStorage.clear();
    notifySuccess.mockClear();
    notifyError.mockClear();
    confirm.mockReset();
    confirm.mockResolvedValue(false);
  });

  it("does not render in browser mode", () => {
    const { container } = render(<DesktopUpdateButton />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a notification when no update is available", async () => {
    window.autoEmailSender = buildDesktopApi({
      checkForUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
    });

    render(<DesktopUpdateButton />);
    fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));

    await waitFor(() => {
      expect(notifySuccess).toHaveBeenCalledWith("检查更新", "当前已是最新版本。");
    });
    expect(confirm).not.toHaveBeenCalled();
  });

  it("shows one notification when desktop update check emits and returns not available", async () => {
    const listeners: Array<(status: DesktopUpdateStatus) => void> = [];
    const status: DesktopUpdateStatus = { state: "not_available", version: "0.1.0" };
    const checkForUpdate = vi.fn(async () => {
      listeners[0]?.(status);
      return status;
    });
    window.autoEmailSender = buildDesktopApi({
      checkForUpdate,
      onUpdateStatus: (callback) => {
        listeners.push(callback);
        return () => undefined;
      },
    });

    render(<DesktopUpdateButton />);
    fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));

    await waitFor(() => expect(checkForUpdate).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByRole("button", { name: /检查更新/ })).not.toBeDisabled());

    expect(notifySuccess).toHaveBeenCalledTimes(1);
    expect(notifySuccess).toHaveBeenCalledWith("检查更新", "当前已是最新版本。");
    expect(confirm).not.toHaveBeenCalled();
  });

  it("shows download choices and keeps NEW when update is available", async () => {
    window.autoEmailSender = buildDesktopApi({
      checkForUpdate: async () => ({
        state: "available",
        version: "0.1.0",
        nextVersion: "0.1.1",
        fullDownloadBytes: 200 * 1024 * 1024,
      }),
    });

    render(<DesktopUpdateButton />);
    fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));

    const downloadButtons = await screen.findAllByRole("button", { name: /下载/ });

    expect(downloadButtons.map((button) => button.textContent)).toEqual([
      "全量下载",
      "差量下载",
      "全量下载",
      "差量下载",
    ]);
    expect(screen.getAllByRole("button", { name: /差量下载/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /全量下载/ }).length).toBeGreaterThan(0);
    expect(screen.getByText(/差量下载：开始后显示实际大小/)).toBeInTheDocument();
    expect(screen.getByText(/全量约 200.0 MB/)).toBeInTheDocument();
    expect(screen.getAllByText(/v0\.1\.1/).length).toBeGreaterThan(0);
    expect(confirm).not.toHaveBeenCalled();
    expect(await screen.findByText("NEW")).toBeInTheDocument();
    expect(window.localStorage.getItem("desktop_pending_update_version")).toBe("0.1.1");
  });

  it("opens a release notes dialog when an update is available", async () => {
    window.autoEmailSender = buildDesktopApi({
      checkForUpdate: async () => ({
        state: "available",
        version: "2.1.5",
        nextVersion: "2.1.6",
        fullDownloadBytes: 200 * 1024 * 1024,
        releaseNotes: "# v2.1.6\n\n## 更新内容\n\n- 修复公告弹窗高度",
      }),
    });

    render(<DesktopUpdateButton />);
    fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));

    expect(await screen.findByRole("dialog", { name: /发现新版本 v2\.1\.6/ })).toBeInTheDocument();
    expect(screen.getByText("更新内容")).toBeInTheDocument();
    expect(screen.getByText("修复公告弹窗高度")).toBeInTheDocument();
    expect(screen.getByTestId("desktop-update-release-notes")).toHaveClass("max-h-[50vh]", "overflow-y-auto");
  });

  it("renders the release notes dialog through a portal on document.body", async () => {
    window.autoEmailSender = buildDesktopApi({
      checkForUpdate: async () => ({
        state: "available",
        version: "2.1.5",
        nextVersion: "2.1.6",
        fullDownloadBytes: 200 * 1024 * 1024,
        releaseNotes: "# v2.1.6\n\n## 更新内容\n\n- 修复公告弹窗高度",
      }),
    });

    const { container } = render(
      <div className="translate-y-10">
        <DesktopUpdateButton />
      </div>,
    );
    fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));

    const dialog = await screen.findByRole("dialog", { name: /发现新版本 v2\.1\.6/ });
    expect(container).not.toHaveTextContent("发现新版本 v2.1.6");
    expect(document.body).toContainElement(dialog);
  });

  it("renders release notes markdown with heading and list styles", async () => {
    window.autoEmailSender = buildDesktopApi({
      checkForUpdate: async () => ({
        state: "available",
        version: "2.1.5",
        nextVersion: "2.1.6",
        fullDownloadBytes: 200 * 1024 * 1024,
        releaseNotes: "# v2.1.6\n\n## 更新内容\n\n- 修复公告弹窗高度\n- 保持 markdown 排版\n\n```ts\nconsole.log('ok');\n```",
      }),
    });

    render(<DesktopUpdateButton />);
    fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));

    const dialog = await screen.findByRole("dialog", { name: /发现新版本 v2\.1\.6/ });
    const releaseNotes = within(dialog).getByTestId("desktop-update-release-notes");
    const markdown = releaseNotes.querySelector("article");
    expect(markdown).toHaveClass("space-y-4", "break-words");
    expect(within(dialog).getByRole("heading", { name: "更新内容" })).toBeInTheDocument();
    expect(within(dialog).getAllByRole("listitem")).toHaveLength(2);
    expect(within(dialog).getByText("console.log('ok');")).toBeInTheDocument();
  });

  it("starts the selected download mode from the release notes dialog", async () => {
    const downloadUpdate = vi.fn(async () => ({
      state: "downloaded_pending_install" as const,
      version: "2.1.5",
      nextVersion: "2.1.6",
    }));
    window.autoEmailSender = buildDesktopApi({
      checkForUpdate: async () => ({
        state: "available",
        version: "2.1.5",
        nextVersion: "2.1.6",
        releaseNotes: "- 更新公告",
      }),
      downloadUpdate,
    });

    render(<DesktopUpdateButton />);
    fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));
    const dialog = await screen.findByRole("dialog", { name: /发现新版本/ });
    fireEvent.click(within(dialog).getByRole("button", { name: /全量下载/ }));

    await waitFor(() => {
      expect(downloadUpdate).toHaveBeenCalledWith({ mode: "full" });
    });
    expect(screen.queryByRole("dialog", { name: /发现新版本/ })).not.toBeInTheDocument();
  });

  it("guides macOS users to manually download updates from release notes", async () => {
    const openExternalUrl = vi.fn(async () => undefined);
    const downloadUpdate = vi.fn(async () => ({ state: "not_available" as const, version: "2.3.8" }));
    const quitAndInstall = vi.fn(async () => undefined);
    const releaseUrl = "https://github.com/JunieXD/AutoEmailSender/releases/tag/v2.4.0";
    window.autoEmailSender = buildDesktopApi({
      checkForUpdate: async () => ({
        state: "manual_download_available",
        version: "2.3.8",
        nextVersion: "2.4.0",
        releaseUrl,
        releaseNotes: "- 支持 macOS",
      }),
      downloadUpdate,
      quitAndInstall,
      openExternalUrl,
    });

    render(<DesktopUpdateButton />);
    fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));

    const dialog = await screen.findByRole("dialog", { name: /发现新版本 v2\.4\.0/ });
    expect(within(dialog).getByText("支持 macOS")).toBeInTheDocument();
    expect(screen.getByText(/macOS 需前往 GitHub Releases 手动下载新版安装包/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /差量下载/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /全量下载/ })).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: /前往下载/ }));

    await waitFor(() => {
      expect(openExternalUrl).toHaveBeenCalledWith(releaseUrl);
    });
    expect(downloadUpdate).not.toHaveBeenCalled();
    expect(quitAndInstall).not.toHaveBeenCalled();
  });

  it("uses fallback release notes and keeps the pending marker when users dismiss the dialog", async () => {
    window.autoEmailSender = buildDesktopApi({
      checkForUpdate: async () => ({
        state: "available",
        version: "2.1.5",
        nextVersion: "2.1.6",
      }),
    });

    render(<DesktopUpdateButton />);
    fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));
    expect(await screen.findByText("新版本已发布，更新内容暂不可用。")).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: /稍后/ }));
    expect(screen.queryByRole("dialog", { name: /发现新版本/ })).not.toBeInTheDocument();
    expect(await screen.findByText("NEW")).toBeInTheDocument();
  });

  it("shows persistent update progress without blocking the page", async () => {
    const listeners: Array<(status: DesktopUpdateStatus) => void> = [];
    window.autoEmailSender = buildDesktopApi({
      onUpdateStatus: (callback) => {
        listeners.push(callback);
        return () => undefined;
      },
    });

    render(<DesktopUpdateButton />);
    listeners[0]?.({
      state: "downloading",
      version: "0.1.0",
      nextVersion: "0.1.1",
      percent: 50,
      transferredBytes: 10 * 1024 * 1024,
      totalBytes: 20 * 1024 * 1024,
      remainingBytes: 10 * 1024 * 1024,
      bytesPerSecond: 512 * 1024,
      remainingSeconds: 20,
      mode: "differential",
    });

    expect(await screen.findByText(/差量包：总计 20.0 MB/)).toBeInTheDocument();
    expect(screen.getByText(/差量包：已下载 10.0 MB/)).toBeInTheDocument();
    expect(screen.getByText(/差量包：剩余 10.0 MB/)).toBeInTheDocument();
    expect(screen.getByText(/512.0 KB\/s/)).toBeInTheDocument();
    expect(screen.getByText(/预计 20 秒/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("explains when a differential update falls back to the full installer", async () => {
    const listeners: Array<(status: DesktopUpdateStatus) => void> = [];
    window.autoEmailSender = buildDesktopApi({
      onUpdateStatus: (callback) => {
        listeners.push(callback);
        return () => undefined;
      },
    });

    render(<DesktopUpdateButton />);
    listeners[0]?.({
      state: "downloading",
      version: "2.3.6",
      nextVersion: "2.3.7",
      percent: 37.6,
      transferredBytes: 100 * 1024 * 1024,
      totalBytes: 266 * 1024 * 1024,
      remainingBytes: 166 * 1024 * 1024,
      bytesPerSecond: 12 * 1024 * 1024,
      remainingSeconds: 14,
      mode: "full",
      fallbackFromDifferential: true,
    });

    expect(await screen.findByText(/已切换全量包：总计 266.0 MB/)).toBeInTheDocument();
    expect(screen.getByText("差量更新不可用，已自动改用全量下载。")).toBeInTheDocument();
    expect(screen.getByText(/全量包：已下载 100.0 MB/)).toBeInTheDocument();
    expect(screen.queryByText(/差量包：总计 266.0 MB/)).not.toBeInTheDocument();
  });

  it("allows users to start a full download proactively", async () => {
    const downloadUpdate = vi.fn(async () => ({
      state: "downloaded_pending_install" as const,
      version: "0.1.0",
      nextVersion: "0.1.1",
      fullDownloadBytes: 200,
    }));
    window.autoEmailSender = buildDesktopApi({
      checkForUpdate: async () => ({
        state: "available",
        version: "0.1.0",
        nextVersion: "0.1.1",
        fullDownloadBytes: 200,
      }),
      downloadUpdate,
    });

    render(<DesktopUpdateButton />);
    fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));
    const dialog = await screen.findByRole("dialog", { name: /发现新版本/ });
    fireEvent.click(within(dialog).getByRole("button", { name: /全量下载/ }));

    await waitFor(() => {
      expect(downloadUpdate).toHaveBeenCalledWith({ mode: "full" });
    });
  });

  it("switches slow differential downloads to full download", async () => {
    const listeners: Array<(status: DesktopUpdateStatus) => void> = [];
    const switchToFullDownload = vi.fn(async () => ({
      state: "downloading" as const,
      version: "0.1.0",
      nextVersion: "0.1.1",
      percent: 30,
      transferredBytes: 30,
      totalBytes: 100,
      remainingBytes: 70,
      bytesPerSecond: 10,
      remainingSeconds: 7,
      mode: "full" as const,
    }));
    window.autoEmailSender = buildDesktopApi({
      switchToFullDownload,
      onUpdateStatus: (callback) => {
        listeners.push(callback);
        return () => undefined;
      },
    });

    render(<DesktopUpdateButton />);
    listeners[0]?.({
      state: "slow_download_offered",
      version: "0.1.0",
      nextVersion: "0.1.1",
      fullDownloadBytes: 200,
      percent: 10,
      transferredBytes: 20,
      totalBytes: 100,
      remainingBytes: 80,
      bytesPerSecond: 2,
      remainingSeconds: 40,
      mode: "differential",
    });
    fireEvent.click(await screen.findByRole("button", { name: /切换全量下载/ }));

    await waitFor(() => {
      expect(switchToFullDownload).toHaveBeenCalled();
    });
  });

  it("installs immediately when checking again after download completes", async () => {
    const listeners: Array<(status: DesktopUpdateStatus) => void> = [];
    const quitAndInstall = vi.fn(async () => undefined);
    window.autoEmailSender = buildDesktopApi({
      checkForUpdate: async () => ({
        state: "downloaded_pending_install",
        version: "0.1.0",
        nextVersion: "0.1.1",
        fullDownloadBytes: 200,
      }),
      quitAndInstall,
      onUpdateStatus: (callback) => {
        listeners.push(callback);
        return () => undefined;
      },
    });

    render(<DesktopUpdateButton />);
    listeners[0]?.({
      state: "downloaded_pending_install",
      version: "0.1.0",
      nextVersion: "0.1.1",
      fullDownloadBytes: 200,
    });
    fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));

    await waitFor(() => {
      expect(quitAndInstall).toHaveBeenCalled();
    });
  });

  it("shows an error notification when update check fails", async () => {
    window.autoEmailSender = buildDesktopApi({
      checkForUpdate: async () => {
        throw new Error("invalid update metadata");
      },
    });

    render(<DesktopUpdateButton />);
    fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));

    await waitFor(() => {
      expect(notifyError).toHaveBeenCalledWith("检查更新失败", "invalid update metadata");
    });
    expect(confirm).not.toHaveBeenCalled();
  });

  it("suggests checking the system proxy when update check hits a connection error", async () => {
    window.autoEmailSender = buildDesktopApi({
      checkForUpdate: async () => {
        throw new Error("connection error");
      },
    });

    render(<DesktopUpdateButton />);
    fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));

    await waitFor(() => {
      expect(notifyError).toHaveBeenCalledWith(
        "检查更新失败",
        "connection error。请检查系统代理是否已开启，或确认当前网络可以访问 GitHub。",
      );
    });
  });
});

function buildDesktopApi(overrides: Partial<NonNullable<typeof window.autoEmailSender>> = {}) {
  return {
    backendBaseUrl: "http://127.0.0.1:48123",
    getVersion: async () => "0.1.0",
    checkForUpdate: async () => ({ state: "not_available" as const, version: "0.1.0" }),
    downloadUpdate: async () => ({ state: "not_available" as const, version: "0.1.0" }),
    switchToFullDownload: async () => ({ state: "not_available" as const, version: "0.1.0" }),
    quitAndInstall: async () => undefined,
    onUpdateStatus: () => () => undefined,
    ...overrides,
  };
}
