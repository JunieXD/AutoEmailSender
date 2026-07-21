import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkspaceMessageThread } from "@/components/organisms/WorkspaceMessageThread";
import type { WorkspaceMessageDTO } from "@/types";

const buildMessage = (
  overrides: Partial<WorkspaceMessageDTO> = {},
): WorkspaceMessageDTO => ({
  id: 1,
  direction: "sent",
  subject: "测试主题",
  content: "老师您好 重点内容 表格内容",
  content_html:
    '<p style="color: #111827;">老师您好 <strong>重点内容</strong></p><table><tbody><tr><td>表格内容</td></tr></tbody></table>',
  rfc_message_id: null,
  failure_summary: null,
  reply_headers: null,
  prompt_tokens: null,
  completion_tokens: null,
  total_tokens: null,
  created_at: "2026-04-22T10:00:00Z",
  source_identities: [],
  ...overrides,
});

describe("WorkspaceMessageThread", () => {
  it("renders expanded message HTML with formatting when content_html exists", () => {
    render(<WorkspaceMessageThread messages={[buildMessage()]} />);

    fireEvent.click(screen.getByRole("button", { name: /展开/ }));

    expect(screen.getByText("重点内容").tagName).toBe("STRONG");
    expect(screen.getByText("表格内容").closest("table")).not.toBeNull();
    expect(screen.queryByText("老师您好 重点内容 表格内容")).not.toBeInTheDocument();
  });

  it("keeps sent HTML content visually inside the sent message bubble", () => {
    render(<WorkspaceMessageThread messages={[buildMessage()]} />);

    fireEvent.click(screen.getByRole("button", { name: /展开/ }));

    const htmlContainer = screen.getByText("重点内容").closest("[data-message-html]");

    expect(htmlContainer).toHaveClass("bg-white/10");
    expect(htmlContainer).toHaveClass("text-white/92");
    expect(htmlContainer).toHaveClass("[&_*]:!text-inherit");
    expect(htmlContainer).not.toHaveClass("bg-white");
    expect(htmlContainer).not.toHaveClass("text-stone-900");
  });

  it("uses a wider thread layout that matches the workspace frame", () => {
    render(<WorkspaceMessageThread messages={[buildMessage()]} />);

    expect(screen.getByText("通信记录").closest("[data-message-thread-root]")).toHaveClass(
      "overflow-hidden",
      "max-h-[min(68vh,680px)]",
    );
    expect(screen.getByRole("button", { name: /展开/ }).closest("[data-message-thread-scroll]")).toHaveClass(
      "max-w-6xl",
      "overflow-y-auto",
    );
    expect(screen.getByRole("button", { name: /展开/ })).toHaveClass("max-w-[86%]");
  });

  it("keeps the empty state compact instead of stretching the message area", () => {
    render(<WorkspaceMessageThread messages={[]} />);

    expect(screen.getByText("暂无通信记录").closest("[data-message-thread-scroll]")).not.toHaveClass(
      "min-h-full",
    );
    expect(screen.getByText("暂无通信记录").closest("[data-empty-thread]")).toHaveClass(
      "flex",
      "w-full",
      "justify-center",
    );
    expect(screen.getByText("暂无通信记录").parentElement).toHaveClass("w-full", "max-w-2xl");
  });

  it("shows readable text when summary content still contains html markup", () => {
    render(
      <WorkspaceMessageThread
        messages={[
          buildMessage({
            content:
              '<html><body><p>老师回复</p><p><strong>欢迎继续交流</strong></p></body></html>',
            content_html: null,
          }),
        ]}
      />,
    );

    expect(screen.queryByText(/<html/i)).not.toBeInTheDocument();
    expect(screen.getByText("老师回复 欢迎继续交流")).toBeInTheDocument();
  });

  it("shows reply monitoring status and a manual refresh action", () => {
    const handleRefresh = vi.fn();
    render(
      <WorkspaceMessageThread
        messages={[buildMessage()]}
        monitoringLabel="正在监听回复"
        lastCheckedAt={new Date("2026-04-22T10:30:00Z")}
        refreshing={false}
        newReceivedCount={1}
        onRefresh={handleRefresh}
      />,
    );

    expect(screen.getByText("正在监听回复")).toBeInTheDocument();
    expect(screen.getByText("新回复")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "刷新通信记录" }));

    expect(handleRefresh).toHaveBeenCalledTimes(1);
  });

  it("labels shared message sources and shows identity-level sync warnings", () => {
    const communicationScope = [
      {
        id: 1,
        name: "申请邮箱",
        profile_name: "申请邮箱",
        sender_name: "申请人",
        email_address: "first@example.com",
      },
      {
        id: 2,
        name: "申请邮箱",
        profile_name: "申请邮箱",
        sender_name: "申请人",
        email_address: "second@example.com",
      },
    ];
    render(
      <WorkspaceMessageThread
        messages={[
          buildMessage({
            direction: "received",
            source_identities: communicationScope,
          }),
        ]}
        communicationScope={communicationScope}
        syncWarnings={[
          {
            identity_id: 2,
            identity_name: "申请邮箱",
            message: "IMAP 连接失败",
          },
        ]}
      />,
    );

    expect(screen.getByText("共享通信 · 2 个身份")).toBeInTheDocument();
    expect(
      screen.getByText(
        "由 申请邮箱（first@example.com）、申请邮箱（second@example.com） 收取",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("申请邮箱：IMAP 连接失败")).toBeInTheDocument();
  });
});
