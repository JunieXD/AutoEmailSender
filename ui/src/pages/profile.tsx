import { type MutationState, useMutation, useMutationState, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { Profile, ProfileForm } from "@/api/types";
import { Field } from "@/components/form";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Section } from "./settings";

/** Settings › 个人 › 个人资料 (docs/ui.md#交互). */
export function ProfileSection() {
  const profile = useQuery({ queryKey: ["getProfile"], queryFn: () => call("getProfile", {}) });
  return (
    <Section title="个人资料">
      <p className="text-sm text-muted-foreground">AI 写信和分析匹配度时参考这些内容。输入后自动保存。</p>
      {profile.error && <p className="text-sm text-destructive">个人资料没能加载：{errorMessage(profile.error)}</p>}
      {profile.data && <ProfileFields profile={profile.data} />}
    </Section>
  );
}

const mutationKey = ["updateProfile"];

/** As the core saves it: trimmed, and blank as nothing. */
function filled(text: string): string | null {
  return text.trim() || null;
}

/**
 * Saves as the user types. Saves run one at a time and outlive this page, so the last text wins
 * even after leaving and coming back quickly, and unsaved text is shown again on return.
 */
function ProfileFields({ profile }: { profile: Profile }) {
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationKey,
    scope: { id: "updateProfile" },
    mutationFn: (f: ProfileForm) => call("updateProfile", f),
    // A load still under way may have read the profile before this save, so it must not land afterwards.
    onSuccess: async (_, f) => {
      await queryClient.cancelQueries({ queryKey: ["getProfile"] });
      queryClient.setQueryData<Profile>(["getProfile"], {
        name: filled(f.name),
        researchInterests: filled(f.researchInterests),
        rewritePreference: filled(f.rewritePreference),
      });
    },
  });
  const last = useMutationState<MutationState<null, Error, ProfileForm>>({
    filters: { mutationKey },
    select: (m) => m.state,
  }).at(-1);
  const unsaved = last?.status === "success" ? undefined : last?.variables;
  const [form, setForm] = useState<ProfileForm>(
    () =>
      unsaved ?? {
        name: profile.name ?? "",
        researchInterests: profile.researchInterests ?? "",
        rewritePreference: profile.rewritePreference ?? "",
      },
  );
  function edit(next: ProfileForm) {
    setForm(next);
    save.mutate(next);
  }
  return (
    <div className="flex flex-col gap-5">
      <Field label="姓名" id="profile-name">
        <Input
          id="profile-name"
          autoComplete="off"
          value={form.name}
          onChange={(e) => edit({ ...form, name: e.target.value })}
        />
      </Field>
      <Field label="意向研究方向" id="profile-research">
        <Textarea
          id="profile-research"
          rows={3}
          placeholder="比如：自然语言处理，对话系统和大模型的可解释性"
          value={form.researchInterests}
          onChange={(e) => edit({ ...form, researchInterests: e.target.value })}
        />
        <p className="mt-1.5 text-sm text-muted-foreground">分析匹配度时，和材料一起作为依据。</p>
      </Field>
      <Field label="草稿改写偏好" id="profile-rewrite">
        <Textarea
          id="profile-rewrite"
          rows={3}
          placeholder="比如：语气正式但不生硬，300 字以内，不用感叹号"
          value={form.rewritePreference}
          onChange={(e) => edit({ ...form, rewritePreference: e.target.value })}
        />
        <p className="mt-1.5 text-sm text-muted-foreground">AI 写草稿和改写时照着做。</p>
      </Field>
      {last?.status === "error" && (
        <p className="text-sm text-destructive">个人资料未能保存：{errorMessage(last.error)}</p>
      )}
    </div>
  );
}
