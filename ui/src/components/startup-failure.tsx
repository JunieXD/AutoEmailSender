import { useMutation } from "@tanstack/react-query";
import appIcon from "../../../docs/icon.svg";
import { exportLogs } from "@/api/app";
import { errorMessage } from "@/api/call";
import { Button } from "@/components/ui/button";

/** Shown in place of the app when the core could not start (docs/ui.md#页面). */
export function StartupFailure({ reason }: { reason: string }) {
  const exported = useMutation({ mutationFn: exportLogs });
  return (
    <main className="grid h-dvh place-items-center p-6">
      <div className="w-full max-w-[520px] rounded-xl bg-background p-8">
        <img src={appIcon} alt="" className="size-10" />
        <h1 className="mt-5 text-lg font-semibold">Auto Email Sender 未能启动</h1>
        <p className="mt-2 text-sm break-words whitespace-pre-wrap text-muted-foreground">{reason}</p>
        <p className="mt-4 text-sm">请导出日志发给开发者，以便查明原因。</p>
        <div className="mt-6 flex items-center gap-3">
          <Button disabled={exported.isPending} onClick={() => exported.mutate()}>
            导出日志
          </Button>
          {exported.data && <span className="text-sm text-muted-foreground">已导出</span>}
          {exported.isError && <span className="text-sm text-destructive">{errorMessage(exported.error)}</span>}
        </div>
      </div>
    </main>
  );
}
