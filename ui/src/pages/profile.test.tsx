// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import type { Profile, ProfileForm } from "@/api/types";
import { ProfileFields } from "./profile";

const remote = vi.hoisted(() => ({ save: vi.fn<(form: ProfileForm) => Promise<Profile>>() }));
vi.mock("@/api/call", () => ({
  call: (_: string, form: ProfileForm) => remote.save(form),
  errorMessage: (error: Error) => error.message,
}));

function deferredProfile() {
  let complete: ((value: Profile) => void) | undefined;
  const promise = new Promise<Profile>((resolve) => {
    complete = resolve;
  });
  return {
    promise,
    resolve: (value: Profile) => {
      if (!complete) throw new Error("missing profile resolver");
      complete(value);
    },
  };
}

function Fields() {
  const { data } = useQuery<Profile>({ queryKey: ["getProfile"], staleTime: Infinity });
  return data && <ProfileFields profile={data} />;
}

it("keeps the last edit across navigation and uses the saved profile after queued saves", async () => {
  const initial: Profile = { name: "旧姓名", researchInterests: null, rewritePreference: null, onboarded: false };
  const first = deferredProfile();
  const second = deferredProfile();
  remote.save.mockReset().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  const client = new QueryClient();
  client.setQueryData(["getProfile"], initial);
  const oldQuery = deferredProfile();
  void client.fetchQuery({ queryKey: ["getProfile"], queryFn: () => oldQuery.promise });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const render = (visible: boolean) =>
    act(async () => root.render(<QueryClientProvider client={client}>{visible && <Fields />}</QueryClientProvider>));
  const input = () => {
    const node = container.querySelector("input");
    if (!node) throw new Error("missing name input");
    return node;
  };
  const edit = (value: string) =>
    act(async () => {
      const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
      if (!descriptor?.set) throw new Error("missing input value setter");
      descriptor.set.call(input(), value);
      input().dispatchEvent(new Event("input", { bubbles: true }));
    });
  try {
    await render(true);
    await edit("第一次");
    await vi.waitFor(() => expect(remote.save).toHaveBeenCalledTimes(1));
    await edit("最后一次");
    await render(false);
    await render(true);
    expect(input().value).toBe("最后一次");
    expect(remote.save).toHaveBeenCalledTimes(1);
    await act(async () => first.resolve({ ...initial, name: "核心第一次保存值" }));
    await vi.waitFor(() => expect(remote.save).toHaveBeenCalledTimes(2));
    const saved = { ...initial, name: "核心最后保存值", onboarded: true };
    await act(async () => second.resolve(saved));
    await vi.waitFor(() => expect(client.getQueryData(["getProfile"])).toEqual(saved));
    await act(async () => oldQuery.resolve(initial));
    await render(false);
    await render(true);
    expect(input().value).toBe(saved.name);
  } finally {
    await act(async () => root.unmount());
    client.clear();
    container.remove();
  }
});
