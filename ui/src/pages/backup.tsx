import { useMutation } from "@tanstack/react-query";
import { cancelRestore, exportBackup, prepareRestore, restoreBackup } from "@/api/app";
import { errorMessage } from "@/api/call";
import type { RestoreInfo } from "@/api/types";
import { scheduledAt } from "@/components/schedule";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { Section } from "./settings";

/** Settings › 应用 › 备份 (docs/ui.md#交互). */
export function BackupSection() {
  const exported = useMutation({ mutationFn: exportBackup });
  const prepare = useMutation({ mutationFn: prepareRestore });
  return (
    <Section title="备份">
      <p className="text-sm text-muted-foreground">
        把全部数据导出成一个文件，换电脑或者数据丢了时用它恢复。备份里没有邮箱授权码、API
        Key、抓取时保存的网页和日志，恢复后要重新填授权码和 API Key。
      </p>
      <div className="flex flex-col items-start gap-1.5">
        <Button disabled={exported.isPending} onClick={() => exported.mutate()}>
          {exported.isPending ? "正在导出…" : "导出备份"}
        </Button>
        {exported.data && <p className="text-sm text-muted-foreground">已导出</p>}
        {exported.error && <p className="text-sm text-destructive">未能导出：{errorMessage(exported.error)}</p>}
      </div>
      <div className="flex flex-col items-start gap-1.5">
        <Button variant="quiet" disabled={prepare.isPending} onClick={() => prepare.mutate()}>
          {prepare.isPending ? "正在检查备份…" : "从备份恢复"}
        </Button>
        {prepare.error && <p className="text-sm text-destructive">无法恢复：{errorMessage(prepare.error)}</p>}
      </div>
      {prepare.data && <RestoreDialog info={prepare.data} onClose={() => prepare.reset()} />}
    </Section>
  );
}

/** Confirms replacing all the data with the backup checked, which restarts the app. */
function RestoreDialog({ info, onClose }: { info: RestoreInfo; onClose: () => void }) {
  const cancel = useMutation({ mutationFn: cancelRestore, onSuccess: onClose });
  const restore = useMutation({ mutationFn: restoreBackup });
  const busy = cancel.isPending || restore.isPending;
  return (
    <Dialog open onOpenChange={(open) => !open && !busy && cancel.mutate()}>
      <DialogContent>
        <DialogHeader>恢复备份</DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-4 text-sm">
            <p>
              这份备份导出于 {scheduledAt(info.exportedAt)}，有 {info.mentorCount} 位导师、{info.emailCount} 封邮件。
            </p>
            <ul className="list-disc pl-5 leading-relaxed">
              <li>
                会替换这台电脑上现有的全部数据。替换前的数据先备份到
                <span className="break-all">“{info.backupDirectory}”</span>。
              </li>
              <li>恢复后要重新填写各个邮箱的授权码和模型的 API Key，填好之前不收发信。</li>
              <li>旧电脑上的应用请先从托盘或菜单栏的图标退出（只关窗口它还会在后台发信），免得同一封信发两次。</li>
              <li>恢复完应用会自动重启。</li>
            </ul>
          </div>
        </DialogBody>
        <DialogFooter>
          <p className="grow text-sm text-destructive" role="alert">
            {(cancel.error ?? restore.error) && errorMessage(cancel.error ?? restore.error)}
          </p>
          <Button variant="ghost" disabled={busy} onClick={() => cancel.mutate()}>
            取消
          </Button>
          <Button disabled={busy} onClick={() => restore.mutate()}>
            {restore.isPending ? "正在恢复…" : "恢复并重启"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
