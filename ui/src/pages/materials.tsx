import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { Material, MaterialPurpose, Purpose } from "@/api/types";
import { Confirm } from "@/components/batch-bar";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogBody, DialogContent, DialogHeader } from "@/components/ui/dialog";
import { fileSize } from "@/format";
import { Section } from "./settings";

/** Settings › 个人 › 材料 (docs/ui.md#交互). */
export function MaterialsSection() {
  const queryClient = useQueryClient();
  const materials = useQuery({ queryKey: ["listMaterials"], queryFn: () => call("listMaterials", {}) });
  const picker = useRef<HTMLInputElement>(null);
  const add = useMutation({
    mutationFn: async (file: File) => call("addMaterial", { fileName: file.name, data: await base64(file) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["listMaterials"] }),
  });
  return (
    <Section title="材料">
      <p className="text-sm text-muted-foreground">
        简历、成绩单这类材料。分析匹配度和 AI 写信时参考材料里的文字，勾选了“默认附件”的随信发出。
      </p>
      {materials.error && <p className="text-sm text-destructive">材料未能加载：{errorMessage(materials.error)}</p>}
      {materials.data && materials.data.length > 0 && (
        <ul className="flex flex-col divide-y divide-border">
          {materials.data.map((m) => (
            <MaterialRow key={m.id} material={m} />
          ))}
        </ul>
      )}
      <div className="flex flex-col items-start gap-1.5">
        <input
          ref={picker}
          type="file"
          accept=".pdf,.docx,.doc"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            // Cleared, so that picking the same file again adds it again.
            e.target.value = "";
            if (file) add.mutate(file);
          }}
        />
        <Button disabled={add.isPending} onClick={() => picker.current?.click()}>
          {add.isPending ? "正在提取文字…" : "添加材料"}
        </Button>
        {add.error && <p className="text-sm text-destructive">{errorMessage(add.error)}</p>}
      </div>
    </Section>
  );
}

/** The file's bytes as base64, which JSON can carry. */
async function base64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  // In slices, as a call takes only so many arguments.
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

const PURPOSES: [Purpose, string][] = [
  ["matching", "匹配分析"],
  ["writing", "写信参考"],
  ["attach_by_default", "默认附件"],
];

function MaterialRow({ material: m }: { material: Material }) {
  const queryClient = useQueryClient();
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["listMaterials"] });
  const purpose = useMutation({
    mutationFn: (input: MaterialPurpose) => call("setMaterialPurpose", input),
    onSettled: refresh,
  });
  const remove = useMutation({ mutationFn: () => call("deleteMaterial", { id: m.id }), onSuccess: refresh });
  const [reading, setReading] = useState(false);
  const on: Record<Purpose, boolean> = {
    matching: m.forMatching,
    writing: m.forWriting,
    attach_by_default: m.attachByDefault,
  };
  const error = purpose.error ?? remove.error;
  return (
    <li className="flex flex-col gap-2.5 py-3">
      <div className="flex items-center gap-2 phone:flex-col phone:items-start">
        <div className="min-w-0 grow">
          <p className="truncate font-medium">{m.fileName}</p>
          <p className="text-sm text-muted-foreground">
            {fileSize(m.size)}
            {!m.hasText && <span className="text-warning">，未提取到文字，只能作附件</span>}
          </p>
        </div>
        <div className="flex gap-1">
          {m.hasText && (
            <Button variant="ghost" onClick={() => setReading(true)}>
              查看文字
            </Button>
          )}
          <Confirm
            trigger={
              <Button variant="ghost" disabled={remove.isPending}>
                删除
              </Button>
            }
            message="删除这份材料？文件和提取的文字都会删除。"
            action="删除"
            side="bottom"
            onConfirm={() => remove.mutate()}
          />
        </div>
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-2">
        {PURPOSES.map(([p, label]) => {
          const textless = p !== "attach_by_default" && !m.hasText;
          return (
            <label key={p} className={`flex items-center gap-2.5 text-sm ${textless ? "opacity-40" : ""}`}>
              <Checkbox
                checked={on[p]}
                disabled={purpose.isPending || textless}
                onCheckedChange={(checked) => purpose.mutate({ id: m.id, purpose: p, on: checked })}
              />
              {label}
            </label>
          );
        })}
      </div>
      {error && <p className="text-sm text-destructive">{errorMessage(error)}</p>}
      {reading && <MaterialText id={m.id} fileName={m.fileName} onClose={() => setReading(false)} />}
    </li>
  );
}

/** The text taken out of a material, for the user to check. */
function MaterialText({ id, fileName, onClose }: { id: number; fileName: string; onClose: () => void }) {
  // A material's file never changes, nor its text.
  const text = useQuery({
    queryKey: ["getMaterialText", id],
    queryFn: () => call("getMaterialText", { id }),
    staleTime: Infinity,
  });
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>{fileName}</DialogHeader>
        <DialogBody className="pb-6">
          {text.error && <p className="text-sm text-destructive">文字未能加载：{errorMessage(text.error)}</p>}
          {text.data && <p className="text-sm whitespace-pre-wrap">{text.data}</p>}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
