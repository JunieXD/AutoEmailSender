// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { WelcomePage } from "./welcome";

const opened = vi.hoisted((): string[] => []);
vi.mock("@tauri-apps/plugin-opener", () => ({
  openUrl: async (url: string) => {
    opened.push(url);
  },
}));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  useNavigate: () => () => undefined,
}));
vi.mock("@/api/call", () => ({
  errorMessage: (error: Error) => error.message,
  // Every step but the test letter done, so that it is the one open.
  call: async (op: string) => {
    if (op === "getProfile") return { name: "林晓", school: null, major: null, research: null };
    if (op === "listMailboxes") return [{ id: 1, address: "me@example.com" }];
    if (op === "listModelConfigs") return [{ id: 1, name: "模型", model: "model" }];
    if (op === "listMaterials") return [{ id: 1, fileName: "简历.pdf" }];
    if (op === "listTemplates") return [{ id: 1, name: "默认模板", isDefault: true }];
    if (op === "listMentors") return { mentors: [], total: 0 };
    if (op === "listTasks") return { tasks: [], total: 0 };
    if (op === "testLetter")
      return {
        subject: "申请读研",
        bodyMd: "王老师，我的主页是 [个人主页](https://me.example.com/)。",
        attachments: [],
        templateName: "默认模板",
      };
    if (op === "getLetterStyle")
      return { font: null, fontSizePt: 12, lineHeight: 1.5, paragraphSpacingEm: 1, firstLineIndent: false };
    throw new Error(`unexpected operation: ${op}`);
  },
}));

it("opens a link in the test letter in the system's browser, not in place of the app", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () =>
      root.render(
        <QueryClientProvider client={client}>
          <WelcomePage />
        </QueryClientProvider>,
      ),
    );
    const link = await vi.waitFor(() => {
      const node = container.querySelector<HTMLAnchorElement>("article a");
      if (!node) throw new Error("no link");
      return node;
    });
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    await act(async () => link.dispatchEvent(click));
    expect(click.defaultPrevented).toBe(true);
    expect(opened).toEqual(["https://me.example.com/"]);
  } finally {
    await act(async () => root.unmount());
    client.clear();
    container.remove();
  }
});
