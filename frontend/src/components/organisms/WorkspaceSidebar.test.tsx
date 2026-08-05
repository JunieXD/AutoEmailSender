import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WorkspaceThreadDTO } from "@/types";
import { WorkspaceSidebar } from "./WorkspaceSidebar";

afterEach(() => {
  window.autoEmailSender = undefined;
});

const buildThread = (
  overrides: Partial<WorkspaceThreadDTO["current_task"]> = {},
): WorkspaceThreadDTO => ({
  professor: {
    id: 1,
    name: "张明",
    email: "zhang@example.edu",
    title: "教授",
    university: "示例大学",
    school: "计算机学院",
    department: "计算机科学与技术系",
    research_direction: "自然语言处理",
    recent_papers: [],
    profile_url: null,
  },
  identity: {
    id: 1,
    name: "默认身份",
    profile_name: "Junie",
    sender_name: "Junie",
    email_address: "junie@example.com",
  },
  llm_profile: {
    id: 1,
    name: "默认模型",
    provider: "openai",
    model_name: "gpt-5.4-mini",
  },
  material_options: [],
  current_task: {
    id: 11,
    source: "personal",
    batch_task_id: null,
    parent_task_id: null,
    status: "matched",
    cancellation_reason: null,
    can_continue_manually: false,
    can_write_follow_up: false,
    outreach_generation_mode: "llm",
    outreach_template_subject: null,
    outreach_template_body_text: null,
    outreach_template_body_html: null,
    rendered_template_subject: null,
    rendered_template_body_text: null,
    rendered_template_body_html: null,
    match_score: 86,
    match_reason: "研究方向与申请材料中的 NLP 项目高度相关。",
    fit_points: ["研究主题重合", "项目经历可迁移"],
    risk_points: ["缺少近期论文互动"],
    match_keywords: ["NLP", "信息抽取"],
    generated_subject: null,
    generated_content_text: null,
    generated_content_html: null,
    draft_generation_source: null,
    draft_fallback_reason: null,
    approved_subject: null,
    approved_body_text: null,
    approved_body_html: null,
    primary_material_id: null,
    primary_material: null,
    selected_material_ids: null,
    approved_at: null,
    scheduled_at: null,
    last_send_attempt_at: null,
    sent_at: null,
    last_rfc_message_id: null,
    retry_count: 0,
    last_error: null,
    is_replied: false,
    estimated_prompt_tokens: null,
    estimated_completion_tokens_upper_bound: null,
    estimated_total_tokens_upper_bound: null,
    last_draft_prompt_tokens: null,
    last_draft_completion_tokens: null,
    last_draft_total_tokens: null,
    draft: {
      subject: null,
      body_text: "",
      body_html: null,
      source: "manual_empty",
      sendable: false,
      editable: true,
    },
    ...overrides,
  },
  messages: [],
  communication_scope: [
    {
      id: 1,
      name: "默认身份",
      profile_name: "Junie",
      sender_name: "Junie",
      email_address: "junie@example.com",
    },
  ],
  sync_warnings: [],
});

describe("WorkspaceSidebar", () => {
  it("shows match analysis details below the professor archive", () => {
    render(<WorkspaceSidebar thread={buildThread()} />);

    expect(screen.getAllByText("匹配分析")).toHaveLength(2);
    expect(screen.getAllByText("86 分")).toHaveLength(2);
    expect(screen.getAllByText("研究方向与申请材料中的 NLP 项目高度相关。")).toHaveLength(2);
    expect(screen.getAllByText("研究主题重合")).toHaveLength(2);
    expect(screen.getAllByText("项目经历可迁移")).toHaveLength(2);
    expect(screen.getAllByText("缺少近期论文互动")).toHaveLength(2);
    expect(screen.getAllByText("NLP")).toHaveLength(2);
    expect(screen.getAllByText("信息抽取")).toHaveLength(2);
  });

  it("shows the professor homepage link when profile url is available", () => {
    const thread = buildThread();

    render(
      <WorkspaceSidebar
        thread={{
          ...thread,
          professor: {
            ...thread.professor,
            profile_url: "https://example.edu/faculty/zhang",
          },
        }}
      />,
    );

    expect(screen.getAllByText("主页")).toHaveLength(2);
    const links = screen.getAllByRole("link", {
      name: "https://example.edu/faculty/zhang",
    });
    expect(links).toHaveLength(2);
    links.forEach((link) => {
      expect(link).toHaveAttribute("href", "https://example.edu/faculty/zhang");
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noreferrer");
    });
  });

  it("hides the professor homepage card when profile url is missing", () => {
    render(<WorkspaceSidebar thread={buildThread()} />);

    expect(screen.queryByText("主页")).not.toBeInTheDocument();
  });

  it("opens the professor homepage with the desktop default browser when available", () => {
    const openExternalUrl = vi.fn().mockResolvedValue(undefined);
    window.autoEmailSender = {
      getVersion: async () => "0.1.0",
      openExternalUrl,
      checkForUpdate: vi.fn(),
      downloadUpdate: vi.fn(),
      switchToFullDownload: vi.fn(),
      quitAndInstall: vi.fn(),
      onUpdateStatus: () => () => undefined,
    };
    const thread = buildThread();

    render(
      <WorkspaceSidebar
        thread={{
          ...thread,
          professor: {
            ...thread.professor,
            profile_url: "https://example.edu/faculty/zhang",
          },
        }}
      />,
    );

    fireEvent.click(
      screen.getAllByRole("link", {
        name: "https://example.edu/faculty/zhang",
      })[0],
    );

    expect(openExternalUrl).toHaveBeenCalledWith("https://example.edu/faculty/zhang");
  });

  it("falls back to the Electron window handler when desktop default browser opening fails", async () => {
    const openExternalUrl = vi.fn().mockRejectedValue(new Error("xdg-open missing"));
    const openWindow = vi.spyOn(window, "open").mockImplementation(() => null);
    window.autoEmailSender = {
      getVersion: async () => "0.1.0",
      openExternalUrl,
      checkForUpdate: vi.fn(),
      downloadUpdate: vi.fn(),
      switchToFullDownload: vi.fn(),
      quitAndInstall: vi.fn(),
      onUpdateStatus: () => () => undefined,
    };
    const thread = buildThread();

    render(
      <WorkspaceSidebar
        thread={{
          ...thread,
          professor: {
            ...thread.professor,
            profile_url: "https://example.edu/faculty/zhang",
          },
        }}
      />,
    );

    fireEvent.click(
      screen.getAllByRole("link", {
        name: "https://example.edu/faculty/zhang",
      })[0],
    );

    await vi.waitFor(() => {
      expect(openWindow).toHaveBeenCalledWith(
        "https://example.edu/faculty/zhang",
        "_blank",
        "noopener,noreferrer",
      );
    });
  });

  it("shows an empty state before match analysis is available", () => {
    render(
      <WorkspaceSidebar
        thread={buildThread({
          match_score: null,
          match_reason: null,
          fit_points: [],
          risk_points: [],
          match_keywords: [],
        })}
      />,
    );

    expect(screen.getAllByText("暂无匹配分析")).toHaveLength(2);
    expect(screen.getAllByText("点击“分析匹配度”后，这里会显示分数、理由和建议。")).toHaveLength(2);
  });
});
