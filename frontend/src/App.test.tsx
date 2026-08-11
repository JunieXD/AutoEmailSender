import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const homePageModuleLoaded = vi.hoisted(() => vi.fn());

vi.mock("@/pages/HomePage", () => {
  homePageModuleLoaded();

  return {
    HomePage: () => <main>首页内容</main>,
  };
});

vi.mock("@/pages/WorkspacePage", () => ({
  WorkspacePage: () => <main>工作区内容</main>,
}));

vi.mock("@/components/organisms/DesktopStartupStatusBanner", () => ({
  DesktopStartupStatusBanner: () => null,
}));

vi.mock("@/components/organisms/RouteScrollRestoration", () => ({
  RouteScrollRestoration: () => null,
}));

vi.mock("@/components/organisms/TopNavBar", () => ({
  TopNavBar: () => <nav>导航栏</nav>,
}));

describe("App route loading", () => {
  beforeEach(() => {
    homePageModuleLoaded.mockClear();
    Reflect.deleteProperty(window, "autoEmailSender");
    window.history.pushState({}, "", "/");
  });

  it("defers page module loading behind a route suspense boundary", async () => {
    render(<App />);

    expect(screen.getByText("页面加载中…")).toBeInTheDocument();
    expect(homePageModuleLoaded).not.toHaveBeenCalled();

    expect(await screen.findByText("首页内容")).toBeInTheDocument();
    expect(homePageModuleLoaded).toHaveBeenCalledTimes(1);

    const scrollContainer = document.querySelector<HTMLElement>(
      '[data-app-scroll-container="true"]',
    );
    expect(scrollContainer).toHaveClass("min-h-0", "flex-1", "overflow-y-auto");
    expect(scrollContainer).toContainElement(screen.getByText("首页内容"));
    expect(scrollContainer).not.toContainElement(screen.getByText("导航栏"));
  });

  it("supports desktop hash routes with navigation blockers", async () => {
    window.autoEmailSender = {
      getVersion: vi.fn(),
      checkForUpdate: vi.fn(),
      downloadUpdate: vi.fn(),
      switchToFullDownload: vi.fn(),
      quitAndInstall: vi.fn(),
      onUpdateStatus: vi.fn(),
    };
    window.history.pushState({}, "", "/#/workspace/21");

    render(<App />);

    expect(await screen.findByText("工作区内容")).toBeInTheDocument();
  });
});
