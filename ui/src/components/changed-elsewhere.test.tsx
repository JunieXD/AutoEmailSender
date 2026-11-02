// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import type { DraftEdit, FirstDraft, FirstDraftEdit, MentorDetail, TaskDraft, TaskSummary } from "@/api/types";
import { FirstLetter } from "./first-letter";
import { TaskLetter } from "./task-letter";

const remote = vi.hoisted(
  (): {
    first: FirstDraft | null;
    task: TaskDraft | null;
    saves: (FirstDraftEdit | DraftEdit)[];
    /** Holds task draft saves back until it resolves. */
    hold: Promise<void>;
  } => ({ first: null, task: null, saves: [], hold: Promise.resolve() }),
);
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}));
vi.mock("@/api/call", () => ({
  errorMessage: (error: Error) => error.message,
  call: async (op: string, input: FirstDraftEdit & DraftEdit) => {
    if (op === "getFirstDraft") return remote.first;
    if (op === "saveFirstDraft") {
      remote.saves.push(input);
      const { subject, bodyMd, attachments } = input;
      remote.first = { id: 3, factCheck: null, subject, bodyMd, attachments };
      return remote.first;
    }
    if (op === "saveTaskDraft") {
      remote.saves.push(input);
      await remote.hold;
      const { subject, bodyMd, attachments } = input;
      remote.task = { ...taskDraft, ...remote.task, subject, bodyMd, attachments };
      return null;
    }
    if (op === "getTaskDraft") return remote.task;
    if (op === "getLetterStyle")
      return { font: null, fontSizePt: 12, lineHeight: 1.5, paragraphSpacingEm: 1, firstLineIndent: false };
    if (op === "getAiFactCheck" || op === "checkMailDomain") return null;
    if (
      op === "listMaterials" ||
      op === "listPlaceholders" ||
      op === "listMailboxes" ||
      op === "checkFirstDraft" ||
      op === "checkTaskDraft" ||
      op === "listModelConfigs" ||
      op === "listModelProviders"
    )
      return [];
    throw new Error(`unexpected operation: ${op}`);
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

const task: TaskSummary = {
  id: 2,
  name: "第一批",
  writer: "ai",
  kind: "first",
  state: "paused",
  createdAt: 0,
  pauseReason: null,
  modelConfigId: null,
  progress: { generating: 0, review: 1, generationFailed: 0, approved: 0, sent: 0, failed: 0, canceled: 0 },
  schedule: { kind: "now" },
};

const taskDraft: TaskDraft = {
  factCheck: null,
  id: 11,
  mentorId: 7,
  subject: "申请读研",
  bodyMd: "原正文",
  attachments: [],
  state: "review",
  error: null,
  cancelReason: null,
  plannedAt: null,
  latestAt: null,
  previous: null,
};

function letter(draft: TaskDraft) {
  return (
    <TaskLetter
      mentor={mentor}
      draft={draft}
      task={task}
      materials={[]}
      placeholders={[]}
      onApproved={() => undefined}
      replacing={false}
    />
  );
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  return {
    client,
    render: (node: ReactNode) =>
      act(async () => root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)),
    editor: () => container.querySelector(".tiptap"),
    body: () => container.querySelector(".tiptap")?.textContent,
    subjectShown: () => container.querySelector<HTMLInputElement>('input[aria-label="主题"]')?.value,
    /** Waits for the last save to end, and for what it brings to be shown. */
    async saved() {
      await vi.waitFor(() => expect(client.getMutationCache().getAll().at(-1)?.state.status).toBe("success"));
      await act(async () => {});
    },
    async subject(value: string) {
      const input = container.querySelector<HTMLInputElement>('input[aria-label="主题"]');
      if (!input) throw new Error("no subject");
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    },
    async close() {
      await act(async () => root.unmount());
      client.clear();
      container.remove();
      remote.first = null;
      remote.task = null;
      remote.saves = [];
      remote.hold = Promise.resolve();
    },
  };
}

it("shows a first letter an Agent saved and does not save the old text back", async () => {
  remote.first = { id: 3, factCheck: null, subject: "申请读研", bodyMd: "原正文", attachments: [] };
  const view = mount();
  try {
    await view.render(<FirstLetter mentor={mentor} />);
    await vi.waitFor(() => expect(view.body()).toBe("原正文"));
    remote.first = { ...remote.first, bodyMd: "Agent 改的正文" };
    await act(async () => view.client.invalidateQueries({ queryKey: ["getFirstDraft", mentor.id] }));
    await vi.waitFor(() => expect(view.body()).toBe("Agent 改的正文"));
    await view.subject("申请读研究生");
    await vi.waitFor(() =>
      expect(remote.saves.at(-1)).toMatchObject({ subject: "申请读研究生", bodyMd: "Agent 改的正文" }),
    );
  } finally {
    await view.close();
  }
});

it("shows a first letter an Agent cleared and does not save the old text back", async () => {
  remote.first = { id: 3, factCheck: null, subject: "申请读研", bodyMd: "原正文", attachments: [] };
  const view = mount();
  try {
    await view.render(<FirstLetter mentor={mentor} />);
    await vi.waitFor(() => expect(view.body()).toBe("原正文"));
    remote.first = null;
    await act(async () => view.client.invalidateQueries({ queryKey: ["getFirstDraft", mentor.id] }));
    await vi.waitFor(() => expect(view.body()).toBe(""));
    expect(view.subjectShown()).toBe("");
    await view.subject("新的主题");
    await vi.waitFor(() => expect(remote.saves.at(-1)).toMatchObject({ subject: "新的主题", bodyMd: "" }));
  } finally {
    await view.close();
  }
});

it("shows a task draft an Agent saved and does not save the old text back", async () => {
  remote.task = taskDraft;
  const view = mount();
  try {
    await view.render(letter(taskDraft));
    await vi.waitFor(() => expect(view.body()).toBe("原正文"));
    remote.task = { ...taskDraft, bodyMd: "Agent 改的正文" };
    await act(async () => view.client.invalidateQueries({ queryKey: ["getTaskDraft", taskDraft.id] }));
    await vi.waitFor(() => expect(view.body()).toBe("Agent 改的正文"));
    await view.subject("申请读研究生");
    await vi.waitFor(() =>
      expect(remote.saves.at(-1)).toMatchObject({ subject: "申请读研究生", bodyMd: "Agent 改的正文" }),
    );
    await view.saved();
    expect(view.subjectShown()).toBe("申请读研究生");
  } finally {
    await view.close();
  }
});

it("shows a task draft an Agent changed back to what this page saved", async () => {
  remote.task = taskDraft;
  const view = mount();
  try {
    await view.render(letter(taskDraft));
    await vi.waitFor(() => expect(view.body()).toBe("原正文"));
    await view.subject("申请读研究生");
    await view.saved();
    const saved = remote.task;
    remote.task = { ...saved, bodyMd: "Agent 改的正文" };
    await act(async () => view.client.invalidateQueries({ queryKey: ["getTaskDraft", taskDraft.id] }));
    await vi.waitFor(() => expect(view.body()).toBe("Agent 改的正文"));
    remote.task = saved;
    await act(async () => view.client.invalidateQueries({ queryKey: ["getTaskDraft", taskDraft.id] }));
    await vi.waitFor(() => expect(view.body()).toBe("原正文"));
  } finally {
    await view.close();
  }
});

it("keeps the letter being typed in when an Agent changed it while a save was under way", async () => {
  remote.task = taskDraft;
  const view = mount();
  try {
    await view.render(letter(taskDraft));
    await vi.waitFor(() => expect(view.body()).toBe("原正文"));
    const editor = view.editor();
    let release: (() => void) | undefined;
    remote.hold = new Promise((resolve) => {
      release = resolve;
    });
    await view.subject("申请读研究生");
    const agent = { ...taskDraft, bodyMd: "Agent 改的正文" };
    remote.task = agent;
    await act(async () => view.client.invalidateQueries({ queryKey: ["getTaskDraft", taskDraft.id] }));
    // The review page has read the Agent's letter, and has yet to read the one this save stores over it.
    await view.render(letter(agent));
    release?.();
    await view.saved();
    expect(view.editor()).toBe(editor);
    expect(view.body()).toBe("原正文");
    expect(view.subjectShown()).toBe("申请读研究生");
  } finally {
    await view.close();
  }
});
