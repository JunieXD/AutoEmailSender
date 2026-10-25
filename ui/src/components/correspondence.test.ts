import { expect, it } from "vitest";
import type { FollowUpEntry } from "@/api/types";
import { followUpActions } from "./correspondence";

const now = 1_800_000_000_000;
const sent: FollowUpEntry = {
  reason: null,
  pending: false,
  previous: {
    messageId: "<previous@example.com>",
    subject: "申请",
    sentAt: now - 5 * 86_400_000,
    fromAddress: "me@example.com",
    fromName: null,
  },
};
it("offers follow-up and a new letter for an unanswered sent email", () => {
  expect(followUpActions(sent, now)).toEqual({ message: "已发出 5 天，还没回复", followUp: true, newLetter: true });
});
it("keeps the reason visible and only offers a new letter when a thread is blocked", () => {
  for (const reason of ["上一封被退回，请先核对邮箱", "上一封缺少邮件编号，无法在原线程追问"]) {
    expect(followUpActions({ ...sent, reason }, now)).toEqual({ message: reason, followUp: false, newLetter: true });
  }
});
it("hides both actions while another first or follow-up email is unsent", () => {
  for (const reason of [null, "上一封说不清发没发出，请先在往来里确认", "还有一封没发出的首封或追问"]) {
    const actions = followUpActions({ ...sent, pending: true, reason }, now);
    expect(actions.followUp).toBe(false);
    expect(actions.newLetter).toBe(false);
  }
});
