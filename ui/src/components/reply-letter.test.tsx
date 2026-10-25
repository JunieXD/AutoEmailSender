// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import type { Letter, MentorDetail, ReplyDraft, ReplyDraftEdit, ReplyTarget } from "@/api/types";
import { dropDrafts } from "./mentor";
import { ReplyLetter } from "./reply-letter";

const remote = vi.hoisted(
  (): {
    target: ReplyTarget;
    draft: ReplyDraft | null;
    write?: (input: ReplyDraftEdit) => Promise<Letter>;
  } => ({
    target: {
      id: 1,
      messageId: "<first@example.edu>",
      fromAddress: "mentor@example.edu",
      fromName: "王伟",
      subject: "面谈",
      sentAt: 1,
    },
    draft: null,
  }),
);
vi.mock("@tanstack/react-router", () => ({ Link: ({ children }: { children: ReactNode }) => <span>{children}</span> }));
vi.mock("@/api/call", () => ({
  errorMessage: (error: Error) => error.message,
  call: async (op: string, input: ReplyDraftEdit) => {
    if (op === "getReplyDraft") return remote.draft;
    if (op === "getReplyTarget") return remote.target;
    if (op === "writeReply" && remote.write) return remote.write(input);
    if (op === "saveReplyDraft") {
      remote.draft = { ...input, id: 5, target: remote.target };
      return remote.draft;
    }
    if (op === "listMaterials" || op === "listPlaceholders" || op === "checkReplyDraft" || op === "listModelProviders")
      return [];
    if (op === "checkMailDomain") return null;
    if (op === "getLetterStyle")
      return { font: null, fontSizePt: 12, lineHeight: 1.5, paragraphSpacingEm: 1, firstLineIndent: false };
    if (op === "listModelConfigs")
      return [{ id: 1, provider: "custom", baseUrl: "http://model.example", agreed: true, isDefault: true }];
    throw new Error(`unexpected operation: ${op}`);
  },
}));

const mentor: MentorDetail = {
  id: 7,
  name: "王伟",
  aliases: [],
  emails: ["mentor@example.edu"],
  affiliations: [],
  homepages: [],
  sourceUrl: null,
  research: null,
  papers: [],
  note: null,
  analysis: { result: null, state: null },
  contact: { status: "replied", count: 1, lastOutAt: 0, lastReplyAt: 1, unread: false },
  tags: [],
  trashed: false,
};

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const render = (visible = true) =>
    act(async () =>
      root.render(
        <QueryClientProvider client={client}>
          {visible && <ReplyLetter mentor={mentor} selected={null} />}
        </QueryClientProvider>,
      ),
    );
  const button = (name: string) => {
    const node = Array.from(container.querySelectorAll("button")).find((b) => b.textContent === name);
    if (!node) throw new Error(`missing button: ${name}`);
    return node;
  };
  return {
    client,
    container,
    render,
    button,
    async close() {
      await act(async () => root.unmount());
      client.clear();
      container.remove();
      remote.write = undefined;
      remote.draft = null;
    },
  };
}

it("does not revive a trashed reply's late AI result or save a new draft after restoration", async () => {
  let complete: ((letter: Letter) => void) | undefined;
  remote.write = () =>
    new Promise((resolve) => {
      complete = resolve;
    });
  const view = setup();
  try {
    await view.render();
    await vi.waitFor(() => expect(view.button("AI 起草").disabled).toBe(false));
    await act(async () => view.button("AI 起草").click());
    await vi.waitFor(() => expect(complete).toBeDefined());
    await view.render(false);
    await act(async () => dropDrafts(view.client, [mentor.id]));
    await act(async () => complete?.({ subject: "", bodyMd: "已放弃的旧 AI 结果" }));
    await view.render();
    await vi.waitFor(() => expect(view.container.querySelector(".tiptap")).not.toBeNull());
    expect(view.container.textContent).not.toContain("已放弃的旧 AI 结果");
    expect(view.container.querySelector(".tiptap")?.textContent).toBe("");
    expect(remote.draft).toBeNull();
  } finally {
    await view.close();
  }
});
