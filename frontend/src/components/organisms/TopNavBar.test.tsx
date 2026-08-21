import { MemoryRouter } from "react-router-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TopNavBar } from "@/components/organisms/TopNavBar";

const selectionMock = vi.hoisted(() => ({
  identities: [] as Array<{ id: number; profile_name?: string | null; name: string; is_default?: boolean }>,
  llmProfiles: [] as Array<{ id: number; name: string; is_default?: boolean }>,
  selectedIdentityId: null as number | null,
  selectedLlmProfileId: null as number | null,
  setSelectedIdentityId: vi.fn(),
  setSelectedLlmProfileId: vi.fn(),
  loading: false,
}));

const draftGuardMock = vi.hoisted(() => ({
  requestWorkspaceDraftGuard: vi.fn(async () => true),
}));
const taskCenterPreloadMock = vi.hoisted(() => vi.fn(async () => undefined));

vi.mock("@/components/molecules/DesktopUpdateButton", () => ({
  DesktopUpdateButton: () => <button type="button">检查更新</button>,
}));

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: () => selectionMock,
}));

vi.mock("@/context/useWorkspaceDraftGuard", () => ({
  useWorkspaceDraftGuard: () => draftGuardMock,
}));

vi.mock("@/app/taskCenterPreload", () => ({
  preloadTaskCenter: taskCenterPreloadMock,
}));

describe("TopNavBar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    selectionMock.identities = [];
    selectionMock.llmProfiles = [];
    selectionMock.selectedIdentityId = null;
    selectionMock.selectedLlmProfileId = null;
    selectionMock.loading = false;
    draftGuardMock.requestWorkspaceDraftGuard.mockResolvedValue(true);
    window.history.pushState({}, "", "/");
  });

  it("includes the statistics panel navigation entry", () => {
    render(
      <MemoryRouter>
        <TopNavBar />
      </MemoryRouter>,
    );

    const link = screen.getByRole("link", { name: "统计面板" });
    expect(link).toHaveAttribute("href", "/dashboard");
    const header = document.querySelector('[data-app-header="true"]');
    expect(header).toHaveClass("shrink-0");
    expect(header).not.toHaveClass("sticky", "backdrop-blur-xl");
  });

  it("keeps the last task center URL when navigating away and back", async () => {
    render(
      <MemoryRouter
        initialEntries={[
          "/tasks?section=delivery&view=attention&q=timeout&search_fields=subject&sort=updated_asc",
        ]}
      >
        <TopNavBar />
      </MemoryRouter>,
    );

    const taskCenterLink = screen.getByRole("link", { name: "任务中心" });
    await waitFor(() => {
      expect(taskCenterLink).toHaveAttribute(
        "href",
        "/tasks?section=delivery&view=attention&q=timeout&search_fields=subject&sort=updated_asc",
      );
    });

    fireEvent.click(screen.getByRole("link", { name: "统计面板" }));

    expect(taskCenterLink).toHaveAttribute(
      "href",
      "/tasks?section=delivery&view=attention&q=timeout&search_fields=subject&sort=updated_asc",
    );
  });

  it("preloads the task center when navigation intent is shown", () => {
    render(
      <MemoryRouter>
        <TopNavBar />
      </MemoryRouter>,
    );

    const taskCenterLink = screen.getByRole("link", { name: "任务中心" });
    fireEvent.mouseEnter(taskCenterLink);

    expect(taskCenterPreloadMock).toHaveBeenCalledTimes(1);
  });

  it("places the community library at the far right after profile", () => {
    render(
      <MemoryRouter>
        <TopNavBar />
      </MemoryRouter>,
    );

    const navLabels = screen
      .getAllByRole("link")
      .map((link) => link.textContent?.replace(/\s+/g, "") ?? "")
      .filter((label) =>
        ["首页", "导师管理", "任务中心", "统计面板", "个人中心", "社区导师库"].includes(label),
      );

    expect(navLabels).toEqual([
      "首页",
      "导师管理",
      "任务中心",
      "统计面板",
      "个人中心",
      "社区导师库",
    ]);
  });

  it("places the QQ group entry immediately before the update button", () => {
    render(
      <MemoryRouter>
        <TopNavBar />
      </MemoryRouter>,
    );

    const qqGroupButton = screen.getByRole("button", { name: "加入 QQ 群" });
    const updateButton = screen.getByRole("button", { name: "检查更新" });

    expect(qqGroupButton.nextElementSibling).toBe(updateButton);
  });

  it("asks the workspace draft guard before switching identity", async () => {
    selectionMock.identities = [
      { id: 1, name: "身份 A", profile_name: "身份 A" },
      { id: 2, name: "身份 B", profile_name: "身份 B" },
    ];
    selectionMock.selectedIdentityId = 1;
    draftGuardMock.requestWorkspaceDraftGuard.mockResolvedValue(false);

    render(
      <MemoryRouter>
        <TopNavBar />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: /身份/ }));
    fireEvent.click(screen.getByRole("option", { name: "身份 B" }));

    await waitFor(() => {
      expect(draftGuardMock.requestWorkspaceDraftGuard).toHaveBeenCalledWith({
        nextIdentityId: 2,
      });
    });
    expect(selectionMock.setSelectedIdentityId).not.toHaveBeenCalled();
  });

  it("asks the workspace draft guard before switching model", async () => {
    selectionMock.llmProfiles = [
      { id: 1, name: "模型 A" },
      { id: 2, name: "模型 B" },
    ];
    selectionMock.selectedLlmProfileId = 1;
    draftGuardMock.requestWorkspaceDraftGuard.mockResolvedValue(false);

    render(
      <MemoryRouter>
        <TopNavBar />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: /模型/ }));
    fireEvent.click(screen.getByRole("option", { name: "模型 B" }));

    await waitFor(() => {
      expect(draftGuardMock.requestWorkspaceDraftGuard).toHaveBeenCalledWith({
        nextLlmProfileId: 2,
      });
    });
    expect(selectionMock.setSelectedLlmProfileId).not.toHaveBeenCalled();
  });
});
