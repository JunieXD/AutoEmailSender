// @vitest-environment happy-dom
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, test, vi } from "vitest";
import type { MentorRow } from "@/api/types";
import { MentorTable } from "@/components/mentor-table";

test("the match score opens the mentor's detail", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const mentor: MentorRow = {
    id: 1,
    name: "老师",
    affiliation: null,
    affiliationCount: 0,
    research: "视觉",
    matchState: { result: { score: 88, basisChanged: true }, state: null },
    contact: { status: "none" },
    tags: [],
  };
  const rootRoute = createRootRoute();
  const list = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => <MentorTable mentors={[mentor]} selected={new Set()} selectable onSelect={() => {}} />,
  });
  const detail = createRoute({ getParentRoute: () => rootRoute, path: "/mentors/$mentorId", component: () => "详情" });
  const router = createRouter({ routeTree: rootRoute.addChildren([list, detail]), history: createMemoryHistory() });
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await router.load();
    await act(async () => root.render(<RouterProvider router={router} />));
    const score = [...host.querySelectorAll("span")].find((span) => span.textContent === "88");
    expect(score).toBeDefined();
    await act(async () => {
      score?.click();
      await router.load();
    });
    expect(router.state.location.pathname).toBe("/mentors/1");
  } finally {
    await act(async () => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
  }
});
