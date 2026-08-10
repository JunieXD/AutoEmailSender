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
  agents: [
    {
      id: "codex",
      name: "Codex",
      detected: true,
      state: "not_installed",
      skillPath: "/Users/alice/.agents/skills/auto-email-sender",
      message: "可单独安装",
    },
    {
      id: "claude_code",
      name: "Claude Code",
      detected: false,
      state: "not_installed",
      skillPath: "/Users/alice/.claude/skills/auto-email-sender",
      message: "可单独安装",
    },
    {
      id: "cursor",
      name: "Cursor",
      detected: false,
      state: "not_installed",
      skillPath: "/Users/alice/.cursor/skills/auto-email-sender",
      message: "可单独安装",
    },
  ],
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
    installAgentSkill: vi.fn(async () => enabledStatus),
    uninstallAgentSkill: vi.fn(async () => enabledStatus),
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
    expect(screen.getByText(/可分析回信、生成重发草稿/)).toBeInTheDocument();
    expect(screen.getByText("Agent 操作说明")).toBeInTheDocument();
    expect(screen.queryByText(/每次 CLI 响应/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "启用命令行" }));

    await waitFor(() => {
      expect(window.autoEmailSender?.enableAgentSupport).toHaveBeenCalledOnce();
      expect(screen.getByText("已启用")).toBeInTheDocument();
    });
    expect(screen.getByText(/新建对话或重启后再使用/)).toBeInTheDocument();
  });

  it("filters Agent rows and installs a selected Agent without stretching the card", async () => {
    installDesktopApi({
      getAgentSupportStatus: vi.fn(async () => enabledStatus),
      installAgentSkill: vi.fn(async () => ({
        ...enabledStatus,
        agents: enabledStatus.agents.map((agent) =>
          agent.id === "claude_code" ? { ...agent, state: "installed" as const, message: "已安装官方 Skill" } : agent,
        ),
      })),
    });
    render(<AgentSupportCard />);

    fireEvent.click(await screen.findByRole("button", { name: /命令行与 Agent/ }));
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索 Agent" }), { target: { value: "Claude" } });
    expect(screen.getByText("Claude Code")).toBeInTheDocument();
    expect(screen.queryByText("Codex")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "安装" }));

    await waitFor(() => {
      expect(window.autoEmailSender?.installAgentSkill).toHaveBeenCalledWith("claude_code");
      expect(screen.getByText("已安装")).toBeInTheDocument();
    });
  });

  it("uses the custom confirmation dialog before disabling Agent support", async () => {
    installDesktopApi({
      getAgentSupportStatus: vi.fn(async () => enabledStatus),
    });
    render(<AgentSupportCard />);

    fireEvent.click(await screen.findByRole("button", { name: /命令行与 Agent/ }));
    fireEvent.click(screen.getByRole("button", { name: "关闭支持" }));

    expect(
      screen.getByRole("dialog", { name: "关闭命令行与 Agent 支持？" }),
    ).toBeInTheDocument();
    expect(window.autoEmailSender?.disableAgentSupport).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "确认关闭" }));

    await waitFor(() => {
      expect(window.autoEmailSender?.disableAgentSupport).toHaveBeenCalledOnce();
    });
  });

  it("keeps the card body mounted until the collapse transition finishes", async () => {
    render(<AgentSupportCard />);

    const toggle = await screen.findByRole("button", { name: /命令行与 Agent/ });
    fireEvent.click(toggle);

    const content = document.getElementById("agent-support-card-content");
    expect(content).toHaveAttribute("data-state", "open");
    expect(content).toHaveClass("collapsible-card-content");

    fireEvent.click(toggle);
    expect(content).toHaveAttribute("data-state", "closed");

    fireEvent.transitionEnd(content as HTMLElement, {
      propertyName: "grid-template-rows",
    });
    expect(document.getElementById("agent-support-card-content")).toBeNull();
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

  it("defaults to installing Codex support when Codex is detected", async () => {
    render(<AgentSupportOnboarding />);

    const codexOption = await screen.findByRole("checkbox", { name: /同时接入 Codex/ });
    expect(codexOption).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "启用并接入 Codex" }));

    await waitFor(() => {
      expect(window.autoEmailSender?.enableAgentSupport).toHaveBeenCalledWith({
        installDetectedAgents: true,
      });
    });
  });

  it("allows enabling only the CLI when the Codex option is cleared", async () => {
    render(<AgentSupportOnboarding />);

    const codexOption = await screen.findByRole("checkbox", { name: /同时接入 Codex/ });
    fireEvent.click(codexOption);
    expect(codexOption).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "仅启用命令行" }));

    await waitFor(() => {
      expect(window.autoEmailSender?.enableAgentSupport).toHaveBeenCalledWith({
        installDetectedAgents: false,
      });
    });
  });
});
