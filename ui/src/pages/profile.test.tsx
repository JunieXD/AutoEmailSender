// @vitest-environment happy-dom
import { defaultScheduler, notifyManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import type { Profile, ProfileForm } from "@/api/types";
import { ProfileFields } from "./profile";
import { WelcomePage } from "./welcome";

const remote = vi.hoisted(() => {
  const profile: Profile = { name: null, researchInterests: null, rewritePreference: null, onboarded: false };
  return {
    save: vi.fn<(form: ProfileForm) => Promise<Profile>>(),
    finish: vi.fn<() => Promise<null>>(),
    navigate: vi.fn<() => Promise<void>>(),
    profile,
  };
});
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  useNavigate: () => remote.navigate,
}));
vi.mock("@/api/call", () => ({
  call: (op: string, form: ProfileForm) => {
    if (op === "updateProfile") return remote.save(form);
    if (op === "finishOnboarding") return remote.finish();
    if (op === "getProfile") return structuredClone(remote.profile);
    throw new Error(`unexpected operation: ${op}`);
  },
  errorMessage: (error: Error) => error.message,
}));

function deferredProfile() {
  let resolve!: (value: Profile) => void;
  const promise = new Promise<Profile>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

function nameInput() {
  return document.querySelector<HTMLInputElement>("#profile-name");
}

function research() {
  return document.querySelector<HTMLTextAreaElement>("#profile-research")?.value;
}

function edit(value: string, field = "#profile-name") {
  return act(async () => {
    const input = document.querySelector<HTMLInputElement | HTMLTextAreaElement>(field);
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set?.call(input, value);
    input?.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

it("keeps the last edit across navigation and uses the saved profile after queued saves", async () => {
  const initial: Profile = { name: "旧姓名", researchInterests: null, rewritePreference: null, onboarded: false };
  const first = deferredProfile();
  const second = deferredProfile();
  remote.save.mockReset().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
  client.setQueryData(["getProfile"], initial);
  const oldQuery = deferredProfile();
  void client.fetchQuery({ queryKey: ["getProfile"], queryFn: () => oldQuery.promise });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const render = (visible: boolean) =>
    act(async () =>
      root.render(<QueryClientProvider client={client}>{visible && <ProfileFields />}</QueryClientProvider>),
    );
  try {
    await render(true);
    await edit("第一次");
    await vi.waitFor(() => expect(remote.save).toHaveBeenCalledTimes(1));
    await edit("最后一次");
    await render(false);
    await render(true);
    expect(nameInput()?.value).toBe("最后一次");
    expect(remote.save).toHaveBeenCalledTimes(1);
    await act(async () => first.resolve({ ...initial, name: "核心第一次保存值" }));
    await vi.waitFor(() => expect(remote.save).toHaveBeenCalledTimes(2));
    const saved = { ...initial, name: "核心最后保存值", onboarded: true };
    await act(async () => second.resolve(saved));
    await vi.waitFor(() => expect(client.getQueryData(["getProfile"])).toEqual(saved));
    await act(async () => oldQuery.resolve(initial));
    await render(false);
    await render(true);
    expect(nameInput()?.value).toBe(saved.name);
  } finally {
    await act(async () => root.unmount());
    client.clear();
    container.remove();
  }
});

it("finishes onboarding after an in-flight profile save and keeps the completed state", async () => {
  const initial: Profile = { name: null, researchInterests: null, rewritePreference: null, onboarded: false };
  remote.profile = initial;
  const saving = deferredProfile();
  remote.save.mockReset().mockReturnValueOnce(saving.promise);
  remote.finish.mockReset().mockImplementation(async () => {
    remote.profile = { ...remote.profile, onboarded: true };
    return null;
  });
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
  client.setQueryData(["getProfile"], initial);
  for (const key of ["listMailboxes", "listModelConfigs", "listMaterials", "listTemplates"]) {
    client.setQueryData([key], []);
  }
  client.setQueryData(["listMentors", {}], { items: [], total: 0 });
  client.setQueryData(["listTasks", 1], { items: [], total: 0 });
  remote.navigate.mockReset().mockImplementation(async () => {
    expect(client.getQueryData<Profile>(["getProfile"])?.onboarded).toBe(true);
  });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () =>
      root.render(
        <QueryClientProvider client={client}>
          <WelcomePage />
        </QueryClientProvider>,
      ),
    );
    await edit("林晓");
    await vi.waitFor(() => expect(remote.save).toHaveBeenCalledTimes(1));
    const finish = Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "完成");
    await act(async () => finish?.click());
    expect(remote.finish).not.toHaveBeenCalled();
    expect(remote.navigate).not.toHaveBeenCalled();
    remote.profile = { ...initial, name: "林晓" };
    await act(async () => saving.resolve(remote.profile));
    await vi.waitFor(() => expect(remote.navigate).toHaveBeenCalledWith({ to: "/mentors" }));
    expect(remote.finish).toHaveBeenCalledTimes(1);
    expect(client.getQueryData<Profile>(["getProfile"])?.onboarded).toBe(true);
  } finally {
    await act(async () => root.unmount());
    client.clear();
    container.remove();
  }
});

it("saves only what is typed and not saved yet, and shows what an Agent saved", async () => {
  const initial: Profile = { name: "林晓", researchInterests: null, rewritePreference: null, onboarded: false };
  remote.profile = initial;
  // Keeps the text trimmed, as the core does.
  remote.save.mockReset().mockImplementation(async (form) => {
    remote.profile = { ...remote.profile, ...Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v.trim()])) };
    return remote.profile;
  });
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
  client.setQueryData(["getProfile"], initial);
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () =>
      root.render(
        <QueryClientProvider client={client}>
          <ProfileFields />
        </QueryClientProvider>,
      ),
    );
    await edit("视觉\n", "#profile-research");
    await vi.waitFor(() => expect(client.getQueryData<Profile>(["getProfile"])?.researchInterests).toBe("视觉"));
    // Still as typed, so the next line starts where the user left off.
    expect(research()).toBe("视觉\n");
    // An Agent saves while the page is open.
    remote.profile = { ...remote.profile, researchInterests: "多模态学习" };
    await act(async () => client.setQueryData(["getProfile"], remote.profile));
    await vi.waitFor(() => expect(research()).toBe("多模态学习"));
    await edit("林晓晓");
    await vi.waitFor(() => expect(remote.profile.name).toBe("林晓晓"));
    expect(remote.save).toHaveBeenLastCalledWith({ name: "林晓晓" });
    expect(research()).toBe("多模态学习");
  } finally {
    await act(async () => root.unmount());
    client.clear();
    container.remove();
  }
});

it("does not send a field again whose save finished before the page heard of it", async () => {
  const initial: Profile = { name: "林晓", researchInterests: null, rewritePreference: null, onboarded: false };
  remote.profile = initial;
  remote.save.mockReset().mockImplementation(async (form) => {
    remote.profile = { ...remote.profile, ...form };
    return remote.profile;
  });
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
  client.setQueryData(["getProfile"], initial);
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const held: (() => void)[] = [];
  try {
    await act(async () =>
      root.render(
        <QueryClientProvider client={client}>
          <ProfileFields />
        </QueryClientProvider>,
      ),
    );
    // The page renders again only when told, as when the next key comes before that.
    notifyManager.setScheduler((notify) => held.push(notify));
    await edit("视觉", "#profile-research");
    await vi.waitFor(() => expect(client.getMutationCache().getAll().at(-1)?.state.status).toBe("success"));
    remote.profile = { ...remote.profile, researchInterests: "多模态学习" };
    await edit("林晓晓");
    expect(remote.save).toHaveBeenLastCalledWith({ name: "林晓晓" });
  } finally {
    notifyManager.setScheduler(defaultScheduler);
    for (const notify of held) notify();
    await act(async () => root.unmount());
    client.clear();
    container.remove();
  }
});
