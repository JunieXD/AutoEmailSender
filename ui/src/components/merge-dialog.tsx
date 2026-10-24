import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { MentorDetail } from "@/api/types";
import { mergedAffiliationCount } from "@/affiliation";
import { ContactLabel, dropDrafts, placeOf, refreshMentors } from "@/components/mentor";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";

/**
 * Merging a mentor listed twice into the other (docs/data-model.md#合并导师): the user picks which one
 * stays, first the one checked first, and sees what the merge makes of them.
 */
export function MergeDialog({ ids, onClose }: { ids: [number, number]; onClose: () => void }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [keep, setKeep] = useState(ids[0]);
  const other = keep === ids[0] ? ids[1] : ids[0];
  const mentors = useQueries({
    queries: ids.map((id) => ({ queryKey: ["getMentor", id], queryFn: () => call("getMentor", { id }) })),
  });
  const drafts = useQueries({
    queries: ids.map((id) => ({ queryKey: ["getFirstDraft", id], queryFn: () => call("getFirstDraft", { id }) })),
  });
  const merge = useMutation({
    mutationFn: (pair: { keep: number; other: number }) => call("mergeMentors", pair),
    onSuccess: async (_, pair) => {
      queryClient.removeQueries({ queryKey: ["getMentor", pair.other] });
      dropDrafts(queryClient, [pair.other]);
      // The kept mentor's composer must start from the draft it may have taken over; what it failed
      // to save stays to be saved again.
      queryClient.removeQueries({ queryKey: ["getFirstDraft", pair.keep] });
      refreshMentors(queryClient);
      onClose();
      await navigate({ to: "/mentors/$mentorId", params: { mentorId: pair.keep } });
    },
  });
  const [a, b] = mentors.map((q) => q.data);
  const failed = [...mentors, ...drafts].find((q) => q.error)?.error;
  // Which draft goes must be known before anything is merged.
  const ready = a && b && drafts.every((q) => q.isSuccess);
  const byId = (id: number) => (a?.id === id ? a : b);
  const kept = byId(keep);
  const gone = byId(other);
  return (
    <Dialog open onOpenChange={(open) => !open && !merge.isPending && onClose()}>
      <DialogContent>
        <DialogHeader>合并导师</DialogHeader>
        <DialogBody>
          {failed && <p className="text-sm text-destructive">未能加载：{errorMessage(failed)}</p>}
          {ready && kept && gone && (
            <div className="flex flex-col gap-4">
              <p className="text-sm text-muted-foreground">选择保留哪一位，另一位的资料、邮件和往来都并进来。</p>
              <div role="radiogroup" aria-label="保留的导师" className="grid grid-cols-2 gap-3 phone:grid-cols-1">
                {[a, b].map((m) => (
                  <Choice
                    key={m.id}
                    mentor={m}
                    chosen={m.id === keep}
                    disabled={merge.isPending}
                    onChoose={() => setKeep(m.id)}
                  />
                ))}
              </div>
              <ul className="list-disc pl-5 text-sm leading-relaxed">
                <li>{outcome(kept, gone)}</li>
                {drafts.every((q) => q.data) && <li>两位都有没写完的首封，删掉{gone.name}的那封。</li>}
                <li>合并后{gone.name}从导师列表里删除，不进回收站。</li>
              </ul>
            </div>
          )}
        </DialogBody>
        <DialogFooter>
          <p className="grow text-sm text-destructive" role="alert">
            {merge.isError && errorMessage(merge.error)}
          </p>
          <DialogClose render={<Button variant="ghost" />}>取消</DialogClose>
          <Button disabled={!ready || merge.isPending} onClick={() => merge.mutate({ keep, other })}>
            合并
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Choice({
  mentor: m,
  chosen,
  disabled,
  onChoose,
}: {
  mentor: MentorDetail;
  chosen: boolean;
  disabled: boolean;
  onChoose: () => void;
}) {
  const place = m.affiliations.at(0);
  return (
    <button
      type="button"
      role="radio"
      aria-checked={chosen}
      disabled={disabled}
      onClick={onChoose}
      className="flex min-w-0 flex-col items-start gap-1 rounded-xl border border-border px-4 py-3 text-left text-sm aria-checked:border-brand aria-checked:bg-selected"
    >
      <span className="font-medium">
        {m.name}
        {chosen && <span className="ml-2 text-brand-text">保留</span>}
      </span>
      {place && <span className="w-full truncate text-muted-foreground">{placeOf(place)}</span>}
      <span className="w-full truncate text-muted-foreground">{m.emails.join("、") || "没有邮箱"}</span>
      <ContactLabel contact={m.contact} />
    </button>
  );
}

/** What the kept mentor ends up with, in a sentence. */
function outcome(kept: MentorDetail, gone: MentorDetail): string {
  const emails = kept.emails.length + gone.emails.length;
  const places = mergedAffiliationCount(kept.affiliations, gone.affiliations);
  const alias = gone.name !== kept.name ? `，${gone.name}记为别名` : "";
  return `保留${kept.name}：合并后有 ${emails} 个邮箱、${places} 个单位${alias}。`;
}
