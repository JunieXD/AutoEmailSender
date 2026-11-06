// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import type {
  Email,
  Entry,
  FactCheck,
  Letter,
  MentorDetail,
  Message,
  PlanEmail,
  ReplyDraft,
  ReplyDraftEdit,
  ReplyTarget,
} from "@/api/types";
import { Correspondence } from "./correspondence";
import { MergeDialog } from "./merge-dialog";
import { dropDrafts } from "./mentor";
import { ReplyLetter } from "./reply-letter";
import { SendingPlan } from "./sending-plan";

const remote = vi.hoisted(
  (): {
    target: ReplyTarget;
    draft: ReplyDraft | null;
    entries: Entry[];
    plan: PlanEmail | null;
    withdrawn: boolean;
    withdrawnDraft: ReplyDraft | null;
    mergedDraft: ReplyDraft | null;
    merged: boolean;
    approved: { draftId: number; replyTo: string; bodyMd: string } | null;
    saves: ReplyDraftEdit[];
    write?: (input: ReplyDraftEdit) => Promise<Letter>;
    check?: (draft: ReplyDraft) => Promise<FactCheck>;
    listing?: Promise<void>;
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
    entries: [],
    plan: null,
    withdrawn: false,
    withdrawnDraft: null,
    mergedDraft: null,
    merged: false,
    approved: null,
    saves: [],
  }),
);
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  useNavigate: () => () => undefined,
  useSearch: () => ({}),
}));
vi.mock("@/api/call", () => ({
  errorMessage: (error: Error) => error.message,
  call: async (
    op: string,
    input: ReplyDraftEdit & { id?: number; ids?: number[]; column?: string; draftId?: number },
  ) => {
    if (op === "getReplyDraft") return remote.draft;
    if (op === "getReplyTarget") return remote.target;
    if (op === "writeReply" && remote.write) return remote.write(input);
    if (op === "saveReplyDraft") {
      remote.saves.push(input);
      remote.draft = { ...input, id: 5, target: remote.target, factCheck: remote.draft?.factCheck ?? null };
      return remote.draft;
    }
    if (op === "getAiFactCheck") return remote.check !== undefined;
    const draft = remote.draft;
    if (op === "factCheckEmail" && remote.check && draft && draft.id === input.id) {
      draft.factCheck = await remote.check(draft);
      return draft.factCheck;
    }
    if (op === "checkReplyDraft" && remote.draft?.factCheck?.state === "completed") {
      return remote.draft.factCheck.findings
        .filter((f) => input.bodyMd.includes(f.quote))
        .map((f) => ({ kind: "unsupported_claim", ...f }));
    }
    if (op === "listCorrespondence") {
      await remote.listing;
      return remote.entries;
    }
    if (op === "markRead") {
      remote.entries = remote.entries.map((e) =>
        "message" in e && e.message.kind === "reply" && input.ids?.includes(e.message.id)
          ? { message: { ...e.message, unread: false } }
          : e,
      );
      return null;
    }
    if (op === "getFirstDraft" || op === "getFollowUpDraft" || op === "getFollowUpEntry") return null;
    // Dropping a reply's hidden AI withdraws it too.
    if (op === "cancelWriting") return null;
    if (op === "sendingPlan") {
      const emails = input.column === "pending" && remote.plan && !remote.withdrawn ? [remote.plan] : [];
      return { emails, total: emails.length };
    }
    if (op === "withdrawEmail") {
      remote.withdrawn = true;
      if (remote.withdrawnDraft) remote.draft = remote.withdrawnDraft;
      remote.entries = remote.entries.filter((entry) => !("email" in entry));
      return null;
    }
    if (op === "getMentor") return { ...mentor, id: input.id };
    if (op === "previewMerge") return { emails: 1, affiliations: 0, discardedDrafts: [] };
    if (op === "mergeMentors") {
      remote.draft = remote.mergedDraft;
      remote.merged = true;
      return null;
    }
    if (op === "approveReply") {
      if (!remote.draft || input.draftId !== remote.draft.id || input.replyTo !== remote.draft.replyTo)
        throw new Error("批准的不是当前草稿");
      remote.approved = { draftId: remote.draft.id, replyTo: input.replyTo, bodyMd: input.bodyMd };
      remote.draft = null;
      return null;
    }
    if (
      op === "listMaterials" ||
      op === "listPlaceholders" ||
      op === "checkReplyDraft" ||
      op === "checkFollowUpDraft" ||
      op === "listModelProviders"
    )
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
  contact: { status: "replied", count: 1, lastOutAt: 0, lastReplyAt: 1, unread: false },
  tags: [],
  trashed: false,
  library: [],
};

const firstEmail: Email = {
  id: 9,
  kind: "first",
  state: "failed",
  subject: "首封",
  bodyMd: "首封正文",
  attachments: [],
  plannedAt: null,
  latestAt: null,
  resendOf: null,
  resent: false,
  tracking: null,
  error: "发送失败",
};

const reply: Extract<Message, { kind: "reply" }> = {
  id: 1,
  kind: "reply",
  fromAddress: "mentor@example.edu",
  subject: "面谈",
  sentAt: 1,
  text: "来信",
  quote: null,
  truncated: false,
  unread: false,
  canReply: true,
  outcome: null,
  marking: null,
  todoExtraction: null,
};

const received: Entry = { message: reply };

function button(name: string) {
  const node = Array.from(document.querySelectorAll("button")).find((b) => b.textContent === name);
  if (!node) throw new Error(`missing button: ${name}`);
  return node;
}

function setup(correspondence = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const render = (visible = true, extra?: ReactNode) =>
    act(async () =>
      root.render(
        <QueryClientProvider client={client}>
          {visible &&
            (correspondence ? <Correspondence mentor={mentor} /> : <ReplyLetter mentor={mentor} selected={null} />)}
          {extra}
        </QueryClientProvider>,
      ),
    );
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
      remote.check = undefined;
      remote.listing = undefined;
      remote.draft = null;
      remote.entries = [];
      remote.plan = null;
      remote.withdrawn = false;
      remote.withdrawnDraft = null;
      remote.mergedDraft = null;
      remote.merged = false;
      remote.approved = null;
      remote.saves = [];
    },
  };
}

it.each([
  { source: "correspondence", answered: false },
  { source: "correspondence", answered: true },
  { source: "sending plan", answered: false },
  { source: "sending plan", answered: true },
])(
  "keeps unrelated reply AI when withdrawing a first email from $source (answered: $answered)",
  async ({ source, answered }) => {
    remote.entries = [{ email: firstEmail }, received];
    remote.plan = {
      ...firstEmail,
      mentorId: mentor.id,
      name: mentor.name,
      taskId: null,
      taskName: null,
      sentAt: null,
      fromAddress: null,
      cancelReason: null,
      waiting: null,
      cloud: null,
      byCloud: false,
    };
    remote.draft = {
      factCheck: null,
      id: 5,
      replyTo: remote.target.messageId ?? "",
      bodyMd: "王老师，原回复",
      attachments: [],
      target: remote.target,
    };
    let complete: ((letter: Letter) => void) | undefined;
    remote.write = () =>
      new Promise((resolve) => {
        complete = resolve;
      });
    const view = setup(source === "correspondence");
    const render = (visible = true) =>
      view.render(visible, visible && source === "sending plan" ? <SendingPlan /> : undefined);
    try {
      await render();
      await vi.waitFor(() => expect(view.button("AI 改写").disabled).toBe(false));
      await act(async () => view.button("AI 改写").click());
      await vi.waitFor(() => expect(complete).toBeDefined());
      if (answered) {
        await act(async () => complete?.({ subject: "", bodyMd: "王老师，改写后的回复" }));
        await vi.waitFor(() => expect(view.button("采用修改")).toBeDefined());
      }
      await act(async () => view.button("改回草稿").click());
      await vi.waitFor(() => expect(remote.withdrawn).toBe(true));
      await vi.waitFor(() => expect(view.container.textContent).toContain(answered ? "采用修改" : "AI 正在改写"));
      if (!answered) await act(async () => complete?.({ subject: "", bodyMd: "王老师，改写后的回复" }));
      await vi.waitFor(() => expect(view.button("采用修改")).toBeDefined());
      await render(false);
      await render();
      await vi.waitFor(() => expect(view.button("采用修改")).toBeDefined());
      await act(async () => view.button("采用修改").click());
      await vi.waitFor(() => expect(remote.draft?.bodyMd).toBe("王老师，改写后的回复"));
      await vi.waitFor(() => expect(view.container.querySelector(".tiptap")?.textContent).toBe("王老师，改写后的回复"));
    } finally {
      await view.close();
    }
  },
);

it.each(["correspondence", "sending plan"])(
  "discards reply B's late AI when withdrawing reply A from %s, without creating a draft after approving A",
  async (source) => {
    const original = remote.target;
    const email: Email = { ...firstEmail, kind: "reply", bodyMd: "王老师，回复 A" };
    remote.entries = [{ email }, received];
    remote.plan = {
      ...email,
      mentorId: mentor.id,
      name: mentor.name,
      taskId: null,
      taskName: null,
      sentAt: null,
      fromAddress: null,
      cancelReason: null,
      waiting: null,
      cloud: null,
      byCloud: false,
    };
    remote.withdrawnDraft = {
      factCheck: null,
      id: email.id,
      replyTo: original.messageId ?? "",
      bodyMd: email.bodyMd,
      attachments: [],
      target: original,
    };
    remote.target = { ...original, id: 2, messageId: "<b@example.edu>", subject: "来信 B", sentAt: 2 };
    let complete: ((letter: Letter) => void) | undefined;
    remote.write = () =>
      new Promise((resolve) => {
        complete = resolve;
      });
    const view = setup(source === "correspondence");
    try {
      await view.render();
      await vi.waitFor(() => expect(view.button("AI 起草").disabled).toBe(false));
      await act(async () => view.button("AI 起草").click());
      await vi.waitFor(() => expect(complete).toBeDefined());
      expect(view.container.textContent).toContain("回复 王伟“来信 B”");
      if (source === "sending plan") await view.render(false, <SendingPlan />);
      await vi.waitFor(() => expect(view.button("改回草稿").disabled).toBe(false));
      await act(async () => view.button("改回草稿").click());
      await vi.waitFor(() => expect(remote.withdrawn).toBe(true));
      if (source === "sending plan") await view.render();
      await vi.waitFor(() => expect(view.container.querySelector(".tiptap")?.textContent).toBe(email.bodyMd));
      expect(view.container.textContent).toContain("回复 王伟“面谈”");
      await act(async () => complete?.({ subject: "", bodyMd: "不应复活的回复 B" }));
      expect(view.container.textContent).not.toContain("不应复活的回复 B");
      await vi.waitFor(() => expect(view.button("发送").disabled).toBe(false));
      await act(async () => view.button("发送").click());
      await vi.waitFor(() =>
        expect(remote.approved).toEqual({ draftId: email.id, replyTo: original.messageId, bodyMd: email.bodyMd }),
      );
      await vi.waitFor(() => expect(view.button("AI 起草").disabled).toBe(false));
      await vi.waitFor(() => expect(view.container.querySelector(".tiptap")?.textContent).toBe(""));
      expect(remote.draft).toBeNull();
      expect(remote.saves).toEqual([]);
      expect(view.container.textContent).not.toContain("不应复活的回复 B");
      await view.render(false);
      await view.render();
      await vi.waitFor(() => expect(view.container.querySelector(".tiptap")?.textContent).toBe(""));
      expect(remote.draft).toBeNull();
      expect(remote.saves).toEqual([]);
    } finally {
      remote.target = original;
      await view.close();
    }
  },
);

it("discards the kept mentor's hidden reply AI when merging, so approving the adopted draft leaves the editor empty", async () => {
  const original = remote.target;
  const adoptedTarget = { ...original, id: 2, messageId: "<adopted@example.edu>", subject: "合并来的来信" };
  remote.mergedDraft = {
    factCheck: null,
    id: 6,
    replyTo: adoptedTarget.messageId,
    bodyMd: "王老师，合并来的回复",
    attachments: [],
    target: adoptedTarget,
  };
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
    await view.render(true, <MergeDialog ids={[mentor.id, 8]} onClose={() => {}} />);
    await vi.waitFor(() => expect(view.button("合并").disabled).toBe(false));
    await act(async () => view.button("合并").click());
    await vi.waitFor(() => expect(remote.merged).toBe(true));
    await view.render();
    await vi.waitFor(() => expect(view.container.querySelector(".tiptap")?.textContent).toBe("王老师，合并来的回复"));
    await act(async () => complete?.({ subject: "", bodyMd: "不应复活的旧 AI 结果" }));
    expect(view.container.textContent).not.toContain("不应复活的旧 AI 结果");
    await vi.waitFor(() => expect(view.button("发送").disabled).toBe(false));
    await act(async () => view.button("发送").click());
    await vi.waitFor(() =>
      expect(remote.approved).toEqual({ draftId: 6, replyTo: adoptedTarget.messageId, bodyMd: "王老师，合并来的回复" }),
    );
    await vi.waitFor(() => expect(view.container.querySelector(".tiptap")?.textContent).toBe(""));
    expect(remote.draft).toBeNull();
    expect(view.container.textContent).not.toContain("不应复活的旧 AI 结果");
    await view.render(false);
    await view.render();
    await vi.waitFor(() => expect(view.container.querySelector(".tiptap")?.textContent).toBe(""));
    expect(remote.draft).toBeNull();
  } finally {
    await view.close();
  }
});

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

it("keeps a failed empty reply's error and original target after new mail and leaving the page", async () => {
  const original = remote.target;
  let fail: ((error: Error) => void) | undefined;
  remote.write = () =>
    new Promise((_, reject) => {
      fail = reject;
    });
  const view = setup();
  try {
    await view.render();
    await vi.waitFor(() => expect(view.button("AI 起草").disabled).toBe(false));
    await act(async () => view.button("AI 起草").click());
    await vi.waitFor(() => expect(fail).toBeDefined());
    remote.target = { ...original, id: 2, messageId: "<new@example.edu>", subject: "新来信", sentAt: 2 };
    await act(async () => {
      await view.client.invalidateQueries({ queryKey: ["getReplyTarget", mentor.id] });
    });
    expect(view.container.textContent).toContain("回复 王伟“面谈”");
    await act(async () => fail?.(new Error("服务商出错了（503）。稍后再试。")));
    await vi.waitFor(() => expect(view.container.textContent).toContain("AI 撰写未能完成：服务商出错了（503）"));
    expect(view.container.textContent).toContain("回复 王伟“面谈”");
    await view.render(false);
    await view.render();
    await vi.waitFor(() => expect(view.container.textContent).toContain("AI 撰写未能完成：服务商出错了（503）"));
    expect(view.container.textContent).toContain("回复 王伟“面谈”");
    expect(remote.draft).toBeNull();
  } finally {
    remote.target = original;
    await view.close();
  }
});

it("leaves a reply that arrives while the window is in the background unread until it comes back", async () => {
  remote.entries = [received];
  const view = setup(true);
  const inFront = vi.spyOn(document, "hasFocus").mockReturnValue(false);
  const unread = () => remote.entries.some((e) => "message" in e && e.message.kind === "reply" && e.message.unread);
  try {
    await view.render();
    await vi.waitFor(() => expect(view.container.textContent).toContain("来信"));
    remote.entries = [received, { message: { ...reply, id: 2, text: "新来信", sentAt: 2, unread: true } }];
    await act(async () => {
      await view.client.invalidateQueries({ queryKey: ["listCorrespondence"] });
    });
    await vi.waitFor(() => expect(view.container.textContent).toContain("新来信"));
    expect(unread()).toBe(true);
    inFront.mockReturnValue(true);
    await act(async () => window.dispatchEvent(new Event("focus")));
    await vi.waitFor(() => expect(unread()).toBe(false));
  } finally {
    inFront.mockRestore();
    await view.close();
  }
});

it("reads only the replies shown, not one that came in while the list loads again", async () => {
  const read = { message: { ...reply, unread: true } };
  remote.entries = [read, { message: { ...reply, id: 2, text: "新来信", sentAt: 2, unread: true } }];
  let listed: (() => void) | undefined;
  remote.listing = new Promise((resolve) => {
    listed = resolve;
  });
  const view = setup(true);
  const inFront = vi.spyOn(document, "hasFocus").mockReturnValue(true);
  const unread = () =>
    remote.entries.flatMap((e) =>
      "message" in e && e.message.kind === "reply" && e.message.unread ? [e.message.id] : [],
    );
  // Shown from the last visit while the list loads again.
  view.client.setQueryData(["listCorrespondence", mentor.id], [read]);
  try {
    await view.render();
    await vi.waitFor(() => expect(unread()).toEqual([2]));
    await act(async () => listed?.());
    await vi.waitFor(() => expect(unread()).toEqual([]));
  } finally {
    inFront.mockRestore();
    await view.close();
  }
});

it("checks a reply AI drafted once it is saved, and marks what the materials do not support", async () => {
  const body = "王老师您好：我获得了国家奖学金。";
  remote.write = async () => ({ subject: "", bodyMd: body });
  const checked: (string | null)[] = [];
  remote.check = async (draft) => {
    checked.push(draft.bodyMd);
    return { state: "completed", findings: [{ quote: "获得了国家奖学金", reason: "材料里没有提到获奖。" }] };
  };
  const view = setup();
  try {
    await view.render();
    await vi.waitFor(() => expect(view.button("AI 起草").disabled).toBe(false));
    await act(async () => view.button("AI 起草").click());
    await vi.waitFor(() =>
      expect(view.container.textContent).toContain("材料里找不到依据：“获得了国家奖学金”材料里没有提到获奖。"),
    );
    await vi.waitFor(() =>
      expect(view.container.querySelector(".tiptap .bg-warning-soft")?.textContent).toBe("获得了国家奖学金"),
    );
    expect(checked).toEqual([body]);
  } finally {
    await view.close();
  }
});
