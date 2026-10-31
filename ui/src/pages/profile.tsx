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
      {profile.data && <ProfileFields />}
    </Section>
  );
}

const mutationKey = ["updateProfile"];

/**
 * Saves as the user types. A save carries the field typed and those not saved yet, never one saved
 * already, so that what an Agent saved meanwhile stays and shows. Saves run one at a time and
 * outlive this page, so the last text wins even after leaving and coming back quickly, and unsaved
 * text is shown again on return. `basic` leaves out the rewrite preference, as the first-run guide
 * does.
 */
export function ProfileFields({ basic = false }: { basic?: boolean }) {
  const queryClient = useQueryClient();
  // Read here, not passed in, so that a render sees the profile and the saves at the same moment.
  const profile = useQuery({ queryKey: ["getProfile"], queryFn: () => call("getProfile", {}) }).data;
  const save = useMutation({
    mutationKey,
    scope: { id: "updateProfile" },
    mutationFn: (f: ProfileForm) => call("updateProfile", f),
    // A load still under way may have read the profile before this save, so it must not land afterwards.
    onSuccess: async (saved) => {
      await queryClient.cancelQueries({ queryKey: ["getProfile"] });
      queryClient.setQueryData<Profile>(["getProfile"], saved);
    },
  });
  const last = useMutationState<MutationState<Profile, Error, ProfileForm>>({
    filters: { mutationKey },
    select: (m) => m.state,
  }).at(-1);
  // As every save carries what is not saved yet, the last one has all of it.
  const unsaved = (last?.status === "success" ? undefined : last?.variables) ?? {};
  // The text as typed on this visit, which the profile holds trimmed.
  const [typed, setTyped] = useState<ProfileForm>({});
  if (!profile) return null;
  function edit(change: ProfileForm) {
    setTyped({ ...typed, ...change });
    // The last save may have finished since this render.
    const done = queryClient.getMutationCache().findAll({ mutationKey }).at(-1)?.state.status === "success";
    save.mutate({ ...(done ? {} : unsaved), ...change });
  }
  const shown = (key: keyof ProfileForm) => {
    const saved = profile[key] ?? "";
    return unsaved[key] ?? (typed[key]?.trim() === saved ? typed[key] : saved);
  };
  return (
    <div className="flex flex-col gap-5">
      <Field label="姓名" id="profile-name">
        <Input
          id="profile-name"
          autoComplete="off"
          value={shown("name")}
          onChange={(e) => edit({ name: e.target.value })}
        />
      </Field>
      <Field label="意向研究方向" id="profile-research">
        <Textarea
          id="profile-research"
          rows={3}
          placeholder="比如：自然语言处理，对话系统和大模型的可解释性"
          value={shown("researchInterests")}
          onChange={(e) => edit({ researchInterests: e.target.value })}
        />
        <p className="mt-1.5 text-sm text-muted-foreground">分析匹配度时，和材料一起作为依据。</p>
      </Field>
      {!basic && (
        <Field label="草稿改写偏好" id="profile-rewrite">
          <Textarea
            id="profile-rewrite"
            rows={3}
            placeholder="比如：语气正式但不生硬，300 字以内，不用感叹号"
            value={shown("rewritePreference")}
            onChange={(e) => edit({ rewritePreference: e.target.value })}
          />
          <p className="mt-1.5 text-sm text-muted-foreground">AI 写草稿和改写时照着做。</p>
        </Field>
      )}
      {last?.status === "error" && (
        <p className="text-sm text-destructive">个人资料未能保存：{errorMessage(last.error)}</p>
      )}
    </div>
  );
}
