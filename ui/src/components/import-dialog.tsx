import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { exportMentors } from "@/api/app";
import { call, errorMessage } from "@/api/call";
import type {
  Decisions,
  Field,
  Imported,
  PreviewImport,
  Preview,
  ImportSetup as Setup,
  TableFormat,
} from "@/api/types";
import { Differences, FIELD_NAMES } from "@/components/differences";
import { placeOf, refreshMentors } from "@/components/mentor";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { base64 } from "@/file";

type ImportFile = Omit<PreviewImport, "decisions">;

const EMPTY: Decisions = { take: [], separate: [], restore: [] };
const fields = Object.keys(FIELD_NAMES).filter((value): value is Field => value in FIELD_NAMES);
const SELECT = "h-9 max-w-full rounded-md border border-border bg-background px-2 text-sm";

export function ImportDialog({ onClose, onImported }: { onClose: () => void; onImported: (result: Imported) => void }) {
  const queryClient = useQueryClient();
  const picker = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<ImportFile | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [counts, setCounts] = useState({ created: 0, updated: 0 });
  const [decisions, setDecisions] = useState<Decisions>(EMPTY);
  const chosen = useRef(EMPTY);
  // A late answer must not replace the preview for a newer file or column mapping.
  const revision = useRef(0);
  const read = useMutation({
    mutationFn: async ({ selected, setup }: { selected: File | ImportFile; setup: Setup }) => {
      const version = ++revision.current;
      if (selected instanceof File && selected.size > 20 * 1024 * 1024)
        throw new Error("文件超过 20 MB，请缩小文件后重试");
      const input =
        selected instanceof File
          ? { fileName: selected.name, data: await base64(selected), setup }
          : { ...selected, setup };
      const result = await call("previewImport", input);
      return { version, input, result };
    },
    onSuccess: ({ version, input, result }) => {
      if (version !== revision.current) return;
      setFile({ ...input, setup: { sheet: result.sheet, columns: result.columns.map((c) => c.field) } });
      setPreview(result);
      setDecisions(EMPTY);
      chosen.current = EMPTY;
      setCounts({ created: result.created, updated: result.updated });
    },
  });
  const countVersion = useRef(0);
  const summarize = useMutation({
    mutationFn: async ({ input, choices, version }: { input: ImportFile; choices: Decisions; version: number }) => {
      const result = await call("previewImport", { ...input, decisions: choices });
      return { version, created: result.created, updated: result.updated };
    },
    onSuccess: ({ version, created, updated }) => {
      if (version === countVersion.current) setCounts({ created, updated });
    },
  });
  function choose(next: Decisions | ((previous: Decisions) => Decisions)) {
    const choices = typeof next === "function" ? next(chosen.current) : next;
    chosen.current = choices;
    setDecisions(choices);
    if (file) summarize.mutate({ input: file, choices, version: ++countVersion.current });
  }
  const template = useMutation({
    mutationFn: (format: TableFormat) =>
      exportMentors({ scope: { kind: "ids", value: [] }, format, note: true }, "导师导入模板"),
  });
  const save = useMutation({
    mutationFn: (input: ImportFile) => call("importMentors", { ...input, decisions }),
    onSuccess: (result) => {
      refreshMentors(queryClient);
      void queryClient.invalidateQueries({ queryKey: ["listTags"] });
      onImported(result);
      onClose();
    },
  });
  const busy = read.isPending || save.isPending;
  const takeFile = (selected: File) => {
    countVersion.current++;
    setPreview(null);
    setFile(null);
    setDecisions(EMPTY);
    chosen.current = EMPTY;
    save.reset();
    summarize.reset();
    read.mutate({ selected, setup: { sheet: null, columns: null } });
  };
  function remap(setup: Setup) {
    if (!file) return;
    countVersion.current++;
    summarize.reset();
    save.reset();
    setDecisions(EMPTY);
    chosen.current = EMPTY;
    read.mutate({ selected: file, setup });
  }
  const error = read.error ?? save.error ?? summarize.error ?? template.error;
  const differenceIds = new Set(preview?.differences.map((d) => d.id));
  const possible = counts.created + counts.updated > 0;
  return (
    <Dialog
      open
      onOpenChange={(open, event) => {
        if (open || busy) return;
        if (file && (event.reason === "outside-press" || event.reason === "escape-key")) return;
        onClose();
      }}
    >
      <DialogContent className={preview ? "w-[1040px]" : ""}>
        <DialogHeader>导入表格</DialogHeader>
        <DialogBody className="space-y-5 pb-4">
          <div
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const selected = e.dataTransfer.files[0];
              if (selected && !busy) takeFile(selected);
            }}
            className="flex flex-wrap items-center gap-3 rounded-lg border border-dashed border-border p-4"
          >
            <input
              ref={picker}
              type="file"
              accept=".csv,.xlsx,.xls"
              hidden
              onChange={(e) => {
                const selected = e.target.files?.[0];
                e.target.value = "";
                if (selected) takeFile(selected);
              }}
            />
            <Button variant="quiet" disabled={busy} onClick={() => picker.current?.click()}>
              {file ? "重新选文件" : "选择文件"}
            </Button>
            <p className="min-w-0 flex-1 text-sm break-words text-muted-foreground phone:order-first phone:w-full phone:flex-none">
              {file?.fileName ?? "CSV、XLSX 或 XLS，不超过 20 MB；也可以拖到这里"}
            </p>
            <div className="flex flex-wrap items-center gap-1 text-sm">
              <span className="text-muted-foreground">下载模板</span>
              {(["xlsx", "csv"] as const).map((format) => (
                <Button
                  key={format}
                  variant="ghost"
                  size="sm"
                  disabled={template.isPending}
                  onClick={() => template.mutate(format)}
                >
                  {format.toUpperCase()}
                </Button>
              ))}
            </div>
          </div>
          {!preview && (
            <div className="space-y-1 text-sm text-muted-foreground">
              <p>一行一位导师，姓名必填</p>
              <p>几个邮箱用换行或分号隔开</p>
              <p>第二个单位写在“学校 2”这样的列里</p>
            </div>
          )}
          {read.isPending && (
            <p role="status" className="text-sm text-muted-foreground">
              正在读取并比对表格…
            </p>
          )}
          {preview && (
            <div className={`space-y-5 ${read.isPending ? "pointer-events-none opacity-50" : ""}`}>
              {preview.sheets.length > 1 && (
                <label className="flex items-center gap-3 text-sm">
                  工作表
                  <select
                    aria-label="工作表"
                    className={SELECT}
                    value={preview.sheet}
                    onChange={(e) => remap({ sheet: Number(e.target.value), columns: null })}
                  >
                    {preview.sheets.map((name, n) => (
                      <option key={n} value={n}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <section>
                <h3 className="mb-2 text-sm font-medium">列的对应</h3>
                <div className="flex gap-2 overflow-x-auto pb-2">
                  {preview.columns.map((c, n) => (
                    <label key={n} className="flex w-36 shrink-0 flex-col gap-1.5 rounded-md bg-muted p-2.5 text-sm">
                      <span className="truncate" title={c.header}>
                        {c.header || `第 ${n + 1} 列`}
                      </span>
                      <select
                        aria-label={`第 ${n + 1} 列对应`}
                        className={`${SELECT} ${c.field ? "" : "text-muted-foreground"}`}
                        value={c.field ?? ""}
                        onChange={(e) => {
                          const columns = preview.columns.map((column) => column.field);
                          const field = fields.find((f) => f === e.target.value);
                          columns[n] = field ?? null;
                          remap({ sheet: preview.sheet, columns });
                        }}
                      >
                        <option value="">不导入</option>
                        {Object.entries(FIELD_NAMES).map(([f, name]) => (
                          <option key={f} value={f}>
                            {name}
                          </option>
                        ))}
                      </select>
                    </label>
                  ))}
                </div>
              </section>
              {preview.issue ? (
                <p role="alert" className="text-sm text-destructive">
                  {preview.issue}
                </p>
              ) : (
                <>
                  <div className="flex flex-wrap gap-x-5 gap-y-1 rounded-lg bg-muted p-3 text-sm" role="status">
                    <span>
                      新增 <strong>{preview.created}</strong> 位
                    </span>
                    <span>
                      补充资料 <strong>{preview.supplemented}</strong> 位
                    </span>
                    <span>
                      资料不同 <strong>{differenceIds.size}</strong> 位
                    </span>
                    <span>
                      没有变化 <strong>{preview.unchanged}</strong> 位
                    </span>
                  </div>
                  {preview.differences.length > 0 && (
                    <section>
                      <h3 className="mb-2 font-medium">资料不同</h3>
                      <Differences
                        items={preview.differences}
                        take={decisions.take}
                        onChange={(take) => choose((d) => ({ ...d, take }))}
                      />
                    </section>
                  )}
                  {preview.maybeSame.length > 0 && (
                    <section>
                      <div className="mb-2 flex flex-wrap items-center gap-2">
                        <h3 className="mr-auto font-medium">可能是同一位 · {preview.maybeSame.length} 行</h3>
                        <Button variant="ghost" size="sm" onClick={() => choose((d) => ({ ...d, separate: [] }))}>
                          全部并入
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => choose((d) => ({ ...d, separate: preview.maybeSame.map((m) => m.row) }))}
                        >
                          全部新建
                        </Button>
                      </div>
                      <div className="space-y-2">
                        {preview.maybeSame.map((m) => (
                          <div key={m.row} className="rounded-lg border border-border p-3 text-sm">
                            <div className="grid grid-cols-2 gap-3 phone:grid-cols-1">
                              <p>
                                <span className="mb-1 block text-xs text-muted-foreground">表格 · 第 {m.row} 行</span>
                                {m.name}
                                <span className="block text-muted-foreground">{m.university}</span>
                              </p>
                              <p>
                                <span className="mb-1 block text-xs text-muted-foreground">本地</span>
                                {m.localName}
                                <span className="block break-words text-muted-foreground">
                                  {m.affiliation && placeOf(m.affiliation)}
                                  <br />
                                  {m.emails.join("、") || "未填写邮箱"}
                                </span>
                              </p>
                            </div>
                            <div className="mt-2 flex gap-4">
                              {[false, true].map((separate) => (
                                <label key={String(separate)} className="flex items-center gap-2">
                                  <input
                                    type="radio"
                                    name={`same-${m.row}`}
                                    checked={decisions.separate.includes(m.row) === separate}
                                    onChange={() =>
                                      choose((d) => ({
                                        ...d,
                                        separate: [
                                          ...d.separate.filter((r) => r !== m.row),
                                          ...(separate ? [m.row] : []),
                                        ],
                                      }))
                                    }
                                  />
                                  {separate ? "新建" : "并入"}
                                </label>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    </section>
                  )}
                  {preview.trashed.length > 0 && (
                    <section>
                      <div className="mb-2 flex flex-wrap items-center gap-2">
                        <h3 className="mr-auto font-medium">在回收站里 · {preview.trashed.length} 行</h3>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => choose((d) => ({ ...d, restore: preview.trashed.map((m) => m.row) }))}
                        >
                          全部恢复并更新
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => choose((d) => ({ ...d, restore: [] }))}>
                          全部不导入
                        </Button>
                      </div>
                      <div className="space-y-2">
                        {preview.trashed.map((m) => (
                          <div
                            key={m.row}
                            className="flex flex-wrap items-center gap-3 rounded-lg border border-border p-3 text-sm"
                          >
                            <p className="mr-auto">
                              {m.name} · 第 {m.row} 行
                            </p>
                            {[false, true].map((restore) => (
                              <label key={String(restore)} className="flex items-center gap-2">
                                <input
                                  type="radio"
                                  name={`restore-${m.row}`}
                                  checked={decisions.restore.includes(m.row) === restore}
                                  onChange={() =>
                                    choose((d) => ({
                                      ...d,
                                      restore: [...d.restore.filter((r) => r !== m.row), ...(restore ? [m.row] : [])],
                                    }))
                                  }
                                />
                                {restore ? "恢复并更新" : "不导入"}
                              </label>
                            ))}
                          </div>
                        ))}
                      </div>
                    </section>
                  )}
                  {preview.errors.length > 0 && (
                    <section>
                      <h3 className="mb-2 font-medium">有问题的行 · {preview.errors.length} 行</h3>
                      <p className="mb-2 text-sm text-muted-foreground">这些行不导入，请修改表格后重新导入</p>
                      <ul className="space-y-1 text-sm text-destructive">
                        {preview.errors.map((e) => (
                          <li key={e.row}>
                            第 {e.row} 行：{e.reason}
                          </li>
                        ))}
                      </ul>
                    </section>
                  )}
                </>
              )}
            </div>
          )}
        </DialogBody>
        <DialogFooter className="flex-wrap">
          <p className="min-w-0 grow text-sm text-destructive" role="alert">
            {error && errorMessage(error)}
          </p>
          <DialogClose render={<Button variant="ghost" disabled={busy} />}>取消</DialogClose>
          <Button
            disabled={
              busy || read.isError || summarize.isPending || summarize.isError || !file || !!preview?.issue || !possible
            }
            onClick={() => file && save.mutate(file)}
          >
            {save.isPending
              ? "正在导入…"
              : `导入${preview && possible ? `（新增 ${counts.created} 位，更新 ${counts.updated} 位）` : ""}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
