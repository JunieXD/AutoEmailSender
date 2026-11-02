// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import type { MentorDetail } from "@/api/types";
import { MentorPage } from "./mentor";

const remote = vi.hoisted((): { note: string | null; saves: string[] } => ({ note: "原备注", saves: [] }));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  useParams: () => ({ mentorId: 7 }),
  useNavigate: () => () => undefined,
  useCanGoBack: () => false,
  useRouter: () => ({ history: { back: () => undefined } }),
  useSearch: () => ({}),
}));
vi.mock("@/api/call", () => ({
  errorMessage: (error: Error) => error.message,
  call: async (op: string, input: { note: string }) => {
    if (op === "getMentor") return { ...mentor, note: remote.note };
    if (op === "setMentorNote") {
      remote.saves.push(input.note);
      remote.note = input.note.trim() ? input.note : null;
      return remote.note;
    }
    if (op === "getLetterStyle")
      return { font: null, fontSizePt: 12, lineHeight: 1.5, paragraphSpacingEm: 1, firstLineIndent: false };
    if (op.startsWith("get") || op.startsWith("check") || op === "applyDefaultTemplate") return null;
    // The rest of the page, which this test does not look at.
    return [];
  },
}));

const mentor: MentorDetail = {
  id: 7,
  name: "王伟",
  mailboxId: null,
  aliases: [],
  emails: ["mentor@example.edu"],
  affiliations: [],
  homepages: [],
  sourceUrl: null,
  research: null,
  papers: [],
  note: null,
  analysis: { result: null, state: null },
  contact: { status: "none" },
  tags: [],
  trashed: false,
  library: [],
};

it("shows a note an Agent cleared or wrote and does not save the old one back", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const note = () => container.querySelector<HTMLTextAreaElement>('textarea[aria-label="备注"]');
  try {
    await act(async () =>
      root.render(
        <QueryClientProvider client={client}>
          <MentorPage />
        </QueryClientProvider>,
      ),
    );
    await vi.waitFor(() => expect(note()?.value).toBe("原备注"));
    remote.note = null;
    await act(async () => client.invalidateQueries({ queryKey: ["getMentor", mentor.id] }));
    await vi.waitFor(() => expect(note()?.value).toBe(""));
    remote.note = "Agent 写的备注";
    await act(async () => client.invalidateQueries({ queryKey: ["getMentor", mentor.id] }));
    await vi.waitFor(() => expect(note()?.value).toBe("Agent 写的备注"));
    await act(async () => {
      const box = note();
      if (!box) throw new Error("no note");
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(box, "Agent 写的备注。已读");
      box.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await vi.waitFor(() => expect(remote.saves.at(-1)).toBe("Agent 写的备注。已读"));
  } finally {
    await act(async () => root.unmount());
    client.clear();
    container.remove();
  }
});
