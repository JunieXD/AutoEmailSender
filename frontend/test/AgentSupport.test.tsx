import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AgentSupportCard } from "@/components/molecules/AgentSupportCard";
import { AgentSupportOnboarding } from "@/components/organisms/AgentSupportOnboarding";
import type { DesktopAgentSupportStatus } from "@/types/desktop";

const notEnabledStatus: DesktopAgentSupportStatus = {
  supported: true,
  state: "not_enabled",
  message: "可以启用",
  onboardingPending: true,
  cliCommand: "auto-email-sender",
  cliPath: "/Users/alice/.local/bin/auto-email-sender",
  skillPath: "/Users/alice/.agents/skills/auto-email-sender",
  appVersion: "2.4.1",
  requiresAgentRestart: false,
};

const enabledStatus: DesktopAgentSupportStatus = {
  ...notEnabledStatus,
  state: "enabled",
  message: "已经启用",
  onboardingPending: false,
  requiresAgentRestart: true,
};

function installDesktopApi(overrides: Record<string, unknown> = {}) {
  window.autoEmailSender = {
    getVersion: async () => "2.4.1",
    checkForUpdate: async () => ({ state: "not_available", version: "2.4.1" }),
    downloadUpdate: async () => ({ state: "not_available", version: "2.4.1" }),
    switchToFullDownload: async () => ({ state: "not_available", version: "2.4.1" }),
    quitAndInstall: async () => undefined,
    onUpdateStatus: () => () => undefined,
    getAgentSupportStatus: vi.fn(async () => notEnabledStatus),
    enableAgentSupport: vi.fn(async () => enabledStatus),
    repairAgentSupport: vi.fn(async () => enabledStatus),
    disableAgentSupport: vi.fn(async () => ({ ...notEnabledStatus, onboardingPending: false })),
    dismissAgentSupportOnboarding: vi.fn(async () => ({ ...notEnabledStatus, onboardingPending: false })),
    onAgentSupportStatus: () => () => undefined,
    ...overrides,
  };
}

describe("Agent support UI", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    installDesktopApi();
  });

  it("enables Agent support from the expandable personal-center card", async () => {
    render(<AgentSupportCard />);

    expect(await screen.findByText("未启用")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /命令行与 Agent/ }));
    expect(screen.getByText(/读取全部回信/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "启用" }));

    await waitFor(() => {
      expect(window.autoEmailSender?.enableAgentSupport).toHaveBeenCalledOnce();
      expect(screen.getByText("已启用")).toBeInTheDocument();
    });
    expect(screen.getByText(/新建一个 Agent 对话/)).toBeInTheDocument();
  });

  it("persists the first-run postpone choice while keeping the card available", async () => {
    render(<AgentSupportOnboarding />);

    expect(await screen.findByRole("dialog", { name: "启用命令行与 Agent 支持？" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "暂不启用" }));

    await waitFor(() => {
      expect(window.autoEmailSender?.dismissAgentSupportOnboarding).toHaveBeenCalledOnce();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });
});
