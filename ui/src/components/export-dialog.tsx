import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { exportMentors } from "@/api/app";
import { errorMessage } from "@/api/call";
import type { Scope, TableFormat } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";

export function ExportDialog({ scope, count, onClose }: { scope: Scope; count: number; onClose: () => void }) {
  const [format, setFormat] = useState<TableFormat>("xlsx");
  const [note, setNote] = useState(true);
  const save = useMutation({
    mutationFn: () => exportMentors({ scope, format, note }, "导师表格"),
    onSuccess: (saved) => {
      if (saved) onClose();
    },
  });
  return (
    <Dialog open onOpenChange={(open) => !open && !save.isPending && onClose()}>
      <DialogContent>
        <DialogHeader>导出表格</DialogHeader>
        <DialogBody className="space-y-5">
          <p>导出 {count.toLocaleString()} 位导师</p>
          <fieldset className="flex gap-5 text-sm">
            <legend className="mb-2 text-muted-foreground">格式</legend>
            {(["xlsx", "csv"] as const).map((f) => (
              <label key={f} className="flex items-center gap-2">
                <input type="radio" name="format" value={f} checked={format === f} onChange={() => setFormat(f)} />
                {f.toUpperCase()}
              </label>
            ))}
          </fieldset>
          <div>
            <label className="flex items-center gap-2.5 text-sm">
              <Checkbox checked={note} onCheckedChange={setNote} />
              包含备注
            </label>
            <p className="mt-1.5 text-sm text-muted-foreground">备注是写给自己的，分享表格前请检查备注</p>
          </div>
        </DialogBody>
        <DialogFooter>
          <p className="min-w-0 grow text-sm text-destructive" role="alert">
            {save.error && errorMessage(save.error)}
          </p>
          <DialogClose render={<Button variant="ghost" disabled={save.isPending} />}>取消</DialogClose>
          <Button disabled={save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "正在导出…" : "导出"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
