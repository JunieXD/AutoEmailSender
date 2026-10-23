import { type MutationState, useMutation, useMutationState, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { LetterFont, LetterStyle } from "@/api/types";
import { Field } from "@/components/form";
import { Checkbox } from "@/components/ui/checkbox";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { letterHtml } from "@/letter";
import { Section } from "./settings";

const FONTS: [LetterFont | "", string][] = [
  ["", "跟随收件人的邮箱"],
  ["song", "宋体"],
  ["hei", "黑体"],
];
const SIZES: [number, string][] = [
  [10.5, "五号（10.5 磅）"],
  [12, "小四（12 磅）"],
  [14, "四号（14 磅）"],
];
const LINE_HEIGHTS: [number, string][] = [1.4, 1.6, 1.8, 2].map((h) => [h, `${h} 倍`]);
const SPACINGS: [number, string][] = [
  [0, "无"],
  [0.4, "小"],
  [0.8, "中"],
  [1.2, "大"],
];

const SAMPLE = `王老师：

您好！我是计算机科学与技术专业的本科生林晓，读了您关于对话系统的几篇论文，很希望能在您的课题组攻读硕士学位。

我做过的相关工作：
- 本科毕业设计：基于检索增强的问答系统
- 参与实验室的多轮对话数据标注

随信附上我的简历，期待您的回复。

此致
敬礼！

林晓`;

/** Settings › 个人 › 信件样式 (docs/ui.md#交互). */
export function LetterStyleSection() {
  const style = useQuery({ queryKey: ["getLetterStyle"], queryFn: () => call("getLetterStyle", {}) });
  return (
    <Section title="信件样式">
      <p className="text-sm text-muted-foreground">每封信都用这套样式，改了马上保存；已经批准的信不受影响。</p>
      {style.error && <p className="text-sm text-destructive">信件样式没能加载：{errorMessage(style.error)}</p>}
      {style.data && <LetterStyleFields saved={style.data} />}
    </Section>
  );
}

const mutationKey = ["updateLetterStyle"];

/**
 * Saves each choice. Saves run one at a time and outlive this page, so the last choice wins even
 * after leaving and coming back quickly, and one not yet saved is shown again on return.
 */
function LetterStyleFields({ saved }: { saved: LetterStyle }) {
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationKey,
    scope: { id: "updateLetterStyle" },
    mutationFn: (s: LetterStyle) => call("updateLetterStyle", s),
    // A load still under way may have read the style before this save, so it must not land afterwards.
    onSuccess: async (_, s) => {
      await queryClient.cancelQueries({ queryKey: ["getLetterStyle"] });
      queryClient.setQueryData(["getLetterStyle"], s);
    },
  });
  const last = useMutationState<MutationState<null, Error, LetterStyle>>({
    filters: { mutationKey },
    select: (m) => m.state,
  }).at(-1);
  const [style, setStyle] = useState(last?.status === "success" ? saved : (last?.variables ?? saved));
  function edit(next: LetterStyle) {
    setStyle(next);
    save.mutate(next);
  }
  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-2 gap-x-4 gap-y-5 phone:grid-cols-1">
        <Field label="字体" id="letter-font">
          <NativeSelect
            id="letter-font"
            value={style.font ?? ""}
            onChange={(e) => edit({ ...style, font: FONTS.find(([f]) => f === e.target.value)?.[0] || null })}
          >
            {FONTS.map(([font, label]) => (
              <NativeSelectOption key={font} value={font}>
                {label}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <Field label="字号" id="letter-size">
          <Choice
            id="letter-size"
            value={style.fontSizePt}
            options={SIZES}
            onChange={(fontSizePt) => edit({ ...style, fontSizePt })}
          />
        </Field>
        <Field label="行距" id="letter-line-height">
          <Choice
            id="letter-line-height"
            value={style.lineHeight}
            options={LINE_HEIGHTS}
            onChange={(lineHeight) => edit({ ...style, lineHeight })}
          />
        </Field>
        <Field label="段距" id="letter-spacing">
          <Choice
            id="letter-spacing"
            value={style.paragraphSpacingEm}
            options={SPACINGS}
            onChange={(paragraphSpacingEm) => edit({ ...style, paragraphSpacingEm })}
          />
        </Field>
      </div>
      <label className="flex items-center gap-2.5">
        <Checkbox
          checked={style.firstLineIndent}
          onCheckedChange={(firstLineIndent) => edit({ ...style, firstLineIndent })}
        />
        段首空两格
      </label>
      {last?.status === "error" && (
        <p className="text-sm text-destructive">信件样式未能保存：{errorMessage(last.error)}</p>
      )}
      <div className="flex flex-col gap-1.5">
        <p className="text-sm text-muted-foreground">预览</p>
        {/* On white whatever the app's theme, as mail apps show letters. */}
        <div
          className="rounded-lg border border-border bg-white px-6 py-5 text-black"
          dangerouslySetInnerHTML={{ __html: letterHtml(SAMPLE, style) }}
        />
        {!style.font && <p className="text-sm text-muted-foreground">实际的字体由收件人的邮箱和设备决定。</p>}
      </div>
    </div>
  );
}

function Choice({
  id,
  value,
  options,
  onChange,
}: {
  id: string;
  value: number;
  options: [number, string][];
  onChange: (value: number) => void;
}) {
  return (
    <NativeSelect id={id} value={String(value)} onChange={(e) => onChange(Number(e.target.value))}>
      {options.map(([v, label]) => (
        <NativeSelectOption key={v} value={String(v)}>
          {label}
        </NativeSelectOption>
      ))}
    </NativeSelect>
  );
}
