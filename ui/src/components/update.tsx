import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowDownToLine } from "lucide-react";
import { useState } from "react";
import { downloadUpdate, restartUpdate, updateStatus } from "@/api/app";
import { errorMessage } from "@/api/call";
import type { UpdateStatus } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { day, fileSize } from "@/format";

/** The app's update state, refreshed by the shell on "update-changed" (docs/ui.md#交互). */
export function useUpdate() {
  return useQuery({ queryKey: ["updateStatus"], queryFn: updateStatus, staleTime: Infinity });
}

/** The sidebar entry shown while a newer version is known. */
export function UpdateEntry() {
  const status = useUpdate().data;
  const [open, setOpen] = useState(false);
  if (!status || status.kind === "current") return null;
  return (
    <>
      <button
        className="mb-0.5 flex h-10 items-center gap-3 rounded-md px-3 text-brand-text hover:bg-press"
        onClick={() => setOpen(true)}
      >
        <ArrowDownToLine className="size-[18px]" strokeWidth={1.6} />
        新版本 {status.update.version}
      </button>
      {open && <UpdateDialog status={status} onClose={() => setOpen(false)} />}
    </>
  );
}

/** Release notes and the download, then the restart; downloading goes on after closing. */
export function UpdateDialog({
  status,
  onClose,
}: {
  status: Exclude<UpdateStatus, { kind: "current" }>;
  onClose: () => void;
}) {
  const download = useMutation({ mutationFn: downloadUpdate });
  const restart = useMutation({ mutationFn: restartUpdate });
  const { update } = status;
  const failure = restart.error
    ? errorMessage(restart.error)
    : status.kind === "available" && (status.error ?? (download.error ? errorMessage(download.error) : null));
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="w-[520px]">
        <DialogHeader>新版本 {update.version}</DialogHeader>
        <DialogBody className="flex flex-col gap-3 text-sm">
          {update.publishedAt !== null && <p className="text-muted-foreground">{day(update.publishedAt)}发布</p>}
          <p className="max-h-72 overflow-y-auto leading-relaxed whitespace-pre-line wrap-anywhere">
            {update.notes ?? "这个版本没有写更新说明。"}
          </p>
        </DialogBody>
        <DialogFooter>
          {status.kind === "downloading" ? (
            <Progress downloaded={status.downloaded} total={status.total} />
          ) : (
            <p className="grow text-sm text-destructive" role="alert">
              {failure}
            </p>
          )}
          <Button variant="ghost" onClick={onClose}>
            稍后
          </Button>
          {status.kind === "available" && (
            <Button disabled={download.isPending} onClick={() => download.mutate()}>
              {status.error ? "重新下载" : "下载并安装"}
            </Button>
          )}
          {status.kind === "ready" && (
            <Button disabled={restart.isPending} onClick={() => restart.mutate()}>
              {restart.isPending ? "正在安装…" : "重启并更新"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Progress({ downloaded, total }: { downloaded: number; total: number | null }) {
  return (
    <div className="flex grow flex-col gap-1.5 text-sm text-muted-foreground">
      {total !== null && (
        <span className="h-1.5 max-w-60 rounded-full bg-muted">
          <span className="block h-full rounded-full bg-primary" style={{ width: `${(downloaded / total) * 100}%` }} />
        </span>
      )}
      <span className="tabular-nums">
        已下载 {fileSize(downloaded)}
        {total !== null && ` / ${fileSize(total)}`}
      </span>
    </div>
  );
}
