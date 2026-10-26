import { useMutation, useMutationState, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { Mailbox, Provider, Rotation } from "@/api/types";
import { Confirm } from "@/components/batch-bar";
import { MailboxDialog, useProviders } from "@/components/mailbox-dialog";
import { scheduledAt } from "@/components/schedule";
import { Button } from "@/components/ui/button";
import { Segments } from "@/components/ui/segments";
import { Row, Section } from "./settings";

/** Sends from a paused mailbox again at once (docs/design.md#多邮箱轮转). */
export function useResume() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => call("resumeMailbox", { id }),
    onSuccess: () =>
      Promise.all(
        ["listMailboxes", "sendingPlan", "scheduledWork"].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key] }),
        ),
      ),
  });
}

/** Settings › 邮件 › 邮箱 (docs/ui.md#交互). */
export function MailboxesSection() {
  const mailboxes = useQuery({ queryKey: ["listMailboxes"], queryFn: () => call("listMailboxes", {}) });
  const providers = useProviders();
  const [adding, setAdding] = useState(false);
  const error = mailboxes.error ?? providers.error;
  return (
    <Section title="邮箱">
      <p className="text-sm text-muted-foreground">
        给导师发信、收回信用的邮箱，可以添加多个。授权码只存在这台电脑上，只用来登录邮箱。
      </p>
      {error && <p className="text-sm text-destructive">邮箱没能加载：{errorMessage(error)}</p>}
      {mailboxes.data && providers.data && (
        <>
          {mailboxes.data.length > 1 && <RotationRow />}
          {mailboxes.data.length > 0 && (
            <ul className="flex flex-col divide-y divide-border">
              {mailboxes.data.map((m) => (
                <MailboxRow key={m.id} mailbox={m} providers={providers.data} />
              ))}
            </ul>
          )}
          <div>
            <Button onClick={() => setAdding(true)}>添加邮箱</Button>
          </div>
          {adding && <MailboxDialog mailbox={null} providers={providers.data} onClose={() => setAdding(false)} />}
        </>
      )}
    </Section>
  );
}

/** docs/design.md#多邮箱轮转: how letters are spread over the mailboxes. */
function RotationRow() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["getRotation"], queryFn: () => call("getRotation", {}) });
  const save = useMutation({
    mutationKey: ["setRotation"],
    mutationFn: (rotation: Rotation) => call("setRotation", rotation),
    onSuccess: async (_, rotation) => {
      await queryClient.cancelQueries({ queryKey: ["getRotation"] });
      queryClient.setQueryData(["getRotation"], rotation);
    },
  });
  // A slower earlier save must not land after a later one, even one made after leaving and coming back.
  const saving = useMutationState({ filters: { mutationKey: ["setRotation"], status: "pending" } }).length > 0;
  const error = query.error ?? save.error;
  return (
    <Row label="轮转">
      {query.data && (
        <>
          <Segments
            label="轮转"
            className="self-start"
            value={query.data}
            disabled={saving}
            options={[
              ["failover", "先用一个"],
              ["perEmail", "每封换一个"],
            ]}
            onChange={(rotation) => save.mutate(rotation)}
          />
          <p className="text-sm text-muted-foreground">
            {query.data === "failover"
              ? "按下面的顺序用第一个能发的邮箱，暂停或到了上限再换下一个。"
              : "每封信轮流用最久没发过信的邮箱。"}
            在导师资料里指定了发件邮箱的，总是从指定的邮箱发。
          </p>
        </>
      )}
      {error && <p className="text-sm text-destructive">{errorMessage(error)}</p>}
    </Row>
  );
}

function MailboxRow({ mailbox: m, providers }: { mailbox: Mailbox; providers: Provider[] }) {
  const [editing, setEditing] = useState(false);
  const queryClient = useQueryClient();
  const test = useMutation({ mutationFn: () => call("sendTestMail", { id: m.id }) });
  const remove = useMutation({
    mutationFn: () => call("deleteMailbox", { id: m.id }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["listMailboxes"] }),
  });
  const resume = useResume();
  // A mailbox with servers filled in by hand is known by its server.
  const provider = providers.find((p) => p.id === m.provider)?.name ?? m.smtp.host;
  return (
    <li className="flex flex-col gap-1.5 py-3">
      <div className="flex items-center gap-2 phone:flex-col phone:items-start">
        <div className="min-w-0 grow">
          <p className="truncate font-medium">{m.address}</p>
          <p className="text-sm text-muted-foreground">
            {provider}，每天最多发 {m.dailyLimit} 封
          </p>
          {m.needsSecret && <p className="text-sm text-warning">恢复后需要重新填写授权码，填好之前不收发信。</p>}
          {m.receiveError && <p className="text-sm text-destructive">收信失败：{m.receiveError}</p>}
          {m.pause && (
            <p className="text-sm text-warning">
              发信暂停到 {scheduledAt(m.pause.until)}：{m.pause.reason}
              <Button
                variant="quiet"
                size="sm"
                className="ml-2"
                disabled={resume.isPending}
                onClick={() => resume.mutate(m.id)}
              >
                立即恢复
              </Button>
            </p>
          )}
        </div>
        <div className="flex gap-1">
          <Button variant="quiet" disabled={test.isPending} onClick={() => test.mutate()}>
            发测试信
          </Button>
          <Button variant="ghost" onClick={() => setEditing(true)}>
            编辑
          </Button>
          <Confirm
            trigger={
              <Button variant="ghost" disabled={remove.isPending}>
                删除
              </Button>
            }
            message="删除这个邮箱？应用不再用它发信和收信，从它收到的来往邮件也从应用里删除；邮箱里的邮件不受影响。"
            action="删除"
            side="bottom"
            onConfirm={() => remove.mutate()}
          />
        </div>
      </div>
      <p className="text-sm empty:hidden" aria-live="polite">
        {test.isPending && <span className="text-muted-foreground">正在发送测试信…</span>}
        {test.isSuccess && <span className="text-muted-foreground">测试信已发到 {m.address}，去收件箱看看。</span>}
        {test.isError && <span className="text-destructive">{errorMessage(test.error)}</span>}
        {remove.isError && <span className="text-destructive">{errorMessage(remove.error)}</span>}
        {resume.isError && <span className="text-destructive">{errorMessage(resume.error)}</span>}
      </p>
      {editing && (
        <MailboxDialog
          mailbox={m}
          providers={providers}
          // A test of the old settings says nothing about the new ones.
          onSaved={() => test.reset()}
          onClose={() => setEditing(false)}
        />
      )}
    </li>
  );
}
