import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DesktopStartupStatusBanner } from "@/components/organisms/DesktopStartupStatusBanner";
import type { DesktopBackendStatus } from "@/types/desktop";

const useDesktopBackendMock = vi.fn();

vi.mock("@/context/DesktopBackendContext", () => ({
  useDesktopBackend: () => useDesktopBackendMock(),
}));

describe("DesktopStartupStatusBanner", () => {
  it("keeps synchronous features available while explaining Worker degradation", () => {
    const status: DesktopBackendStatus = {
      state: "degraded",
      baseUrl: "http://127.0.0.1:48120",
      reason: "background_restarting",
      message: "后台服务正在恢复，其他功能仍可使用",
    };
    useDesktopBackendMock.mockReturnValue({ isDesktop: true, status });

    render(<DesktopStartupStatusBanner />);

    expect(screen.getByText(status.message)).toBeInTheDocument();
    expect(screen.getByText(/查询、编辑和其他即时操作仍可正常使用/)).toBeInTheDocument();
  });

  it("shows database version recovery guidance", () => {
    const status: DesktopBackendStatus = {
      state: "error",
      phase: "error",
      message: "系统准备失败",
      elapsedSeconds: 5,
      detail: "当前数据由较新版本创建，当前版本无法直接打开。",
      databaseError: {
        code: "DATABASE_REQUIRES_NEWER_APP",
        message: "当前数据由较新版本创建，当前版本无法直接打开。",
        currentAppVersion: "2.3.0",
        minimumSupportedAppVersion: "2.4.0",
        backupDirectory: "C:\\Users\\Alice\\AppData\\Roaming\\AutoEmailSender\\backups\\schema",
        suggestedActions: ["安装 2.4.0 或更高版本继续使用", "如需回退，请从升级前备份恢复数据库"],
      },
    };
    useDesktopBackendMock.mockReturnValue({ isDesktop: true, status });

    render(<DesktopStartupStatusBanner />);

    expect(screen.getByText("当前数据需要 AutoEmailSender 2.4.0 或更高版本")).toBeInTheDocument();
    expect(screen.getByText(/请升级到新版继续使用/)).toBeInTheDocument();
    expect(screen.getByText("C:\\Users\\Alice\\AppData\\Roaming\\AutoEmailSender\\backups\\schema")).toBeInTheDocument();
  });
});
