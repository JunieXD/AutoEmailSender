import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listen } from "@tauri-apps/api/event";
import { useEffect } from "react";
import { call, errorMessage } from "@/api/call";
import type { AgentRequest } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";

/**
 * What an Agent asked to do that needs the user's say (docs/ui.md 设置 › 应用 › 手机和 Agent), one
 * request at a time, oldest first. The core drops a request after five minutes or when its Agent
 * goes away, and says so with the same event.
 */
export function AgentRequests() {
  const queryClient = useQueryClient();
  const requests = useQuery({ queryKey: ["listAgentRequests"], queryFn: () => call("listAgentRequests", {}) });
  useEffect(() => {
    const stop = listen("agent-requests-changed", () => {
      void queryClient.invalidateQueries({ queryKey: ["listAgentRequests"] });
    });
    return () => void stop.then((f) => f());
  }, [queryClient]);
  const [request, ...queued] = requests.data ?? [];
  return request ? <RequestDialog key={request.id} request={request} queued={queued.length} /> : null;
}

// docs/ui.md: at most five of the mentors are named, the rest counted.
const SHOWN = 5;

function RequestDialog({ request, queued }: { request: AgentRequest; queued: number }) {
  const queryClient = useQueryClient();
  const answer = useMutation({
    mutationFn: (allow: boolean) => call("answerAgentRequest", { id: request.id, allow }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["listAgentRequests"] }),
  });
  // An answered request stays on screen until the list comes back without it.
  const busy = answer.isPending || answer.isSuccess;
  const { action, targets } = request.description;
  return (
    // Closing it is a no: the Agent hears it was refused and can ask again.
    <Dialog open onOpenChange={(open) => !open && !busy && answer.mutate(false)}>
      <DialogContent className="w-[480px]">
        <DialogHeader>Agent 请求</DialogHeader>
        <DialogBody className="space-y-3 py-4">
          <p className="text-base">{action}</p>
          {/* Names and addresses come from the Agent or from web pages, so they stay in their own
              boxes, apart from what the app itself says. */}
          {targets.length > 0 && (
            <ul className="divide-y divide-border rounded-lg border border-border text-sm">
              {targets.slice(0, SHOWN).map((target, i) => (
                <li key={i} className="line-clamp-3 px-3 py-1.5 wrap-anywhere" title={target}>
                  {target}
                </li>
              ))}
              {targets.length > SHOWN && (
                <li className="px-3 py-1.5 text-muted-foreground">等 {targets.length - SHOWN} 位</li>
              )}
            </ul>
          )}
          <p className="text-sm text-muted-foreground">
            这是你接入的 Agent 要做的事，允许后马上执行。5 分钟内不回应，就当作拒绝。
            {queued > 0 && `之后还有 ${queued} 条请求。`}
          </p>
          {answer.error && <p className="text-sm text-destructive">{errorMessage(answer.error)}</p>}
        </DialogBody>
        <DialogFooter className="justify-end">
          <Button variant="ghost" onClick={() => answer.mutate(false)} disabled={busy}>
            拒绝
          </Button>
          <Button onClick={() => answer.mutate(true)} disabled={busy}>
            允许
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
