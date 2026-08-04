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
      state: "not_installed",
      skillPath: "/Users/alice/.agents/skills/auto-email-sender",
      message: "可单独安装",
    },
    {
      id: "claude_code",
      name: "Claude Code",
      state: "not_installed",
      skillPath: "/Users/alice/.claude/skills/auto-email-sender",
      message: "可单独安装",
    },
    {
      id: "cursor",
      name: "Cursor",
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
    expect(screen.getByText(/找出回信中表示没名额的导师/)).toBeInTheDocument();
    expect(screen.getByText("Agent 可以根据当前 CLI 提供的能力操控软件。")).toBeInTheDocument();
    expect(screen.queryByText(/每次 CLI 响应/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "启用命令行" }));

    await waitFor(() => {
      expect(window.autoEmailSender?.enableAgentSupport).toHaveBeenCalledOnce();
      expect(screen.getByText("已启用")).toBeInTheDocument();
    });
    expect(screen.getByText(/新建一个 Agent 对话/)).toBeInTheDocument();
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
});
