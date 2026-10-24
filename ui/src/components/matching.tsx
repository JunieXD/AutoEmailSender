import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { MentorDetail } from "@/api/types";
import { AiIcon, ModelAction } from "@/components/ai-writing";
import { Confirm } from "@/components/batch-bar";
import { refreshMentors } from "@/components/mentor";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";

export function MatchingDialog({ ids, onClose }: { ids: number[]; onClose: () => void }) {
  const [redo, setRedo] = useState(false);
  const queryClient = useQueryClient();
  const estimate = useQuery({
    queryKey: ["planMatching", ids, redo],
    queryFn: () => call("planMatching", { ids, redo }),
  });
  const start = useMutation({
    mutationFn: () => call("startMatching", { ids, redo }),
    onSuccess: onClose,
    onSettled: () => {
      refreshMentors(queryClient);
      void queryClient.invalidateQueries({ queryKey: ["listModelConfigs"] });
    },
  });
  const data = estimate.data;
  return (
    <Dialog open onOpenChange={(open) => !open && !start.isPending && onClose()}>
      <DialogContent className="w-[520px]">
        <DialogHeader>分析匹配度</DialogHeader>
        <DialogBody className="space-y-4 py-4 text-sm">
          {data && (
            <>
              <p className="text-base font-medium">将分析 {data.plan.analyze} 位导师</p>
              <div className="space-y-2 rounded-lg bg-muted p-4">
                <label className="flex items-center gap-2">
                  <Checkbox checked={redo} onCheckedChange={setRedo} disabled={start.isPending} />
                  已有结果 {data.plan.existing} 位，也重新分析
                </label>
                {data.plan.changed > 0 && <p>依据有变化 {data.plan.changed} 位，已列入分析</p>}
                <p>跳过资料不足 {data.plan.insufficient} 位：可以先补充研究方向或代表论文</p>
                <p>跳过已经在排队或分析中 {data.plan.queued} 位</p>
              </div>
              <p>
                模型：{data.model ?? "尚未添加模型"}{" "}
                <Link to="/settings/models" className="ml-2 text-brand-text">
                  去设置
                </Link>
              </p>
              <div className="text-muted-foreground">
                <p>
                  约 {(data.inputTokens + data.outputTokens).toLocaleString()} Token
                  {data.cost !== null && `，约 ${data.cost.toFixed(2)} 元`}
                </p>
                <p className="mt-1 text-xs">按字数估算，实际以服务商为准。</p>
              </div>
              {!data.hasBasis && (
                <p className="text-warning">
                  还没有匹配依据。请在
                  <Link to="/settings/materials" className="text-brand-text">
                    设置 › 个人
                  </Link>
                  里添加用于匹配分析的材料或填写意向研究方向。
                </p>
              )}
            </>
          )}
          {estimate.isPending && <p className="text-muted-foreground">正在计算预计用量…</p>}
          {(estimate.error ?? start.error) && (
            <p role="alert" className="text-destructive">
              {errorMessage(estimate.error ?? start.error)}
            </p>
          )}
        </DialogBody>
        <DialogFooter className="justify-end">
          <DialogClose render={<Button variant="ghost" disabled={start.isPending} />}>取消</DialogClose>
          <ModelAction
            disabled={!data || !data.hasBasis || data.plan.analyze === 0 || start.isPending}
            onWrite={() => start.mutate()}
          >
            <AiIcon />
            开始分析
          </ModelAction>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function MatchButton({ mentor: m }: { mentor: MentorDetail }) {
  const queryClient = useQueryClient();
  const run = useMutation({
    mutationFn: () => call("matchMentor", { id: m.id }),
    onSettled: () => {
      refreshMentors(queryClient);
      void queryClient.invalidateQueries({ queryKey: ["listModelConfigs"] });
    },
  });
  const insufficient = m.research === null && m.papers.length === 0;
  const busy = m.analysis.state?.state === "queued" || m.analysis.state?.state === "running";
  return (
    <div className="space-y-2">
      <ModelAction disabled={insufficient || busy || run.isPending} onWrite={() => run.mutate()}>
        <AiIcon />
        {m.analysis.state?.state === "failed" ? "重试" : m.analysis.result ? "重新分析" : "分析匹配度"}
      </ModelAction>
      {insufficient && <p className="text-muted-foreground">请先补充研究方向或代表论文。</p>}
      {run.error && (
        <p role="alert" className="text-destructive">
          {errorMessage(run.error)}
        </p>
      )}
    </div>
  );
}

export function MatchingProgress() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["matchingProgress"], queryFn: () => call("matchingProgress", {}) });
  const action = useMutation({
    mutationFn: (op: "pauseMatching" | "resumeMatching" | "cancelMatching" | "retryMatching") => call(op, {}),
    onSettled: () => {
      refreshMentors(queryClient);
      void queryClient.invalidateQueries({ queryKey: ["listModelConfigs"] });
    },
  });
  const p = query.data;
  if (!p && !query.error) return null;
  if (p && p.remaining === 0 && p.failed === 0 && !action.error) return null;
  return (
    <div className="mx-7 mb-3 rounded-lg bg-muted px-4 py-3 text-sm phone:mx-4" aria-live="polite">
      {p && (
        <>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <p className="grow">
              {p.remaining > 0
                ? `${p.pause ? "已暂停匹配度分析" : "正在分析匹配度"}，还剩 ${p.remaining} 位`
                : "匹配度分析结束"}
            </p>
            {p.remaining > 0 && (
              <>
                {p.pause ? (
                  <ModelAction disabled={action.isPending} onWrite={() => action.mutate("resumeMatching")}>
                    继续
                  </ModelAction>
                ) : (
                  <Button variant="quiet" disabled={action.isPending} onClick={() => action.mutate("pauseMatching")}>
                    暂停
                  </Button>
                )}
                <Confirm
                  trigger={
                    <Button variant="quiet" disabled={action.isPending}>
                      取消
                    </Button>
                  }
                  message="取消排队中的分析？正在分析的几位会做完，已有结果保留。"
                  action="取消分析"
                  onConfirm={() => action.mutate("cancelMatching")}
                />
              </>
            )}
            {p.failed > 0 && (
              <>
                <span className="text-warning">失败 {p.failed} 位</span>
                <ModelAction disabled={action.isPending} onWrite={() => action.mutate("retryMatching")}>
                  重试
                </ModelAction>
              </>
            )}
          </div>
          {p.pause && p.remaining > 0 && (
            <p className="mt-1 text-muted-foreground wrap-anywhere">
              {p.pause.reason ?? "你已暂停分析，正在分析的几位会做完。"}
            </p>
          )}
        </>
      )}
      {(query.error ?? action.error) && (
        <p role="alert" className="text-destructive">
          {errorMessage(query.error ?? action.error)}
        </p>
      )}
    </div>
  );
}
