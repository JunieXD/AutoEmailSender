import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { RouteScrollRestoration } from "@/components/organisms/RouteScrollRestoration";
import { __resetKeepAliveScrollMemory } from "@/lib/keepAliveRoutes";

/**
 * 注意：'/tasks' 与 '/' 均为 KEEP_ALIVE_PATHS 中的白名单路由，
 * 第一个测试用例使用 '/create-task'（不在白名单中）来验证非保活路由的滚动归零行为。
 */

const scrollToMock = vi.fn<(...args: unknown[]) => void>();

const setDocHeight = (height: number) => {
  Object.defineProperty(document.documentElement, "scrollHeight", {
    configurable: true,
    get: () => height,
  });
  Object.defineProperty(document.body, "scrollHeight", {
    configurable: true,
    get: () => height,
  });
};

const setViewportHeight = (height: number) => {
  Object.defineProperty(window, "innerHeight", {
    configurable: true,
    get: () => height,
  });
};

/**
 * 模拟用户滚动：设置 scrollY，再派发 scroll 事件，触发组件内监听器把当前值写进 map。
 */
const simulateUserScroll = (y: number) => {
  Object.defineProperty(window, "scrollY", {
    configurable: true,
    get: () => y,
  });
  window.dispatchEvent(new Event("scroll"));
};

beforeEach(() => {
  __resetKeepAliveScrollMemory();
  scrollToMock.mockClear();
  Object.defineProperty(window, "scrollY", {
    configurable: true,
    get: () => 0,
  });
  Object.defineProperty(window, "scrollTo", {
    configurable: true,
    value: scrollToMock,
  });
  // 默认给个足够大的文档高度，保证恢复观察器一拍就达标
  setDocHeight(5000);
  setViewportHeight(800);
});

afterEach(() => {
  vi.useRealTimers();
  document.querySelector('[data-app-scroll-container="true"]')?.remove();
});

describe("RouteScrollRestoration 非保活路由", () => {
  it("非保活路由之间切换时仍归零", async () => {
    render(
      <MemoryRouter initialEntries={["/create-task"]}>
        <RouteScrollRestoration />
        <Link to="/test-compose">test-compose</Link>
        <Routes>
          <Route path="/create-task" element={<div>create-task 页面</div>} />
          <Route path="/test-compose" element={<div>test-compose 页面</div>} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(scrollToMock).toHaveBeenCalledWith({
        left: 0,
        top: 0,
        behavior: "auto",
      });
    });

    scrollToMock.mockClear();
    fireEvent.click(screen.getByRole("link", { name: "test-compose" }));

    await waitFor(() => {
      expect(scrollToMock).toHaveBeenCalledWith({
        left: 0,
        top: 0,
        behavior: "auto",
      });
    });
  });
});

describe("RouteScrollRestoration 保活路由", () => {
  it("应用滚动容器存在时只记录并恢复容器位置", async () => {
    const scrollContainer = document.createElement("div");
    scrollContainer.dataset.appScrollContainer = "true";
    let scrollTop = 0;
    Object.defineProperties(scrollContainer, {
      scrollTop: {
        configurable: true,
        get: () => scrollTop,
        set: (value: number) => {
          scrollTop = value;
        },
      },
      scrollHeight: { configurable: true, get: () => 5000 },
      clientHeight: { configurable: true, get: () => 800 },
    });
    const containerScrollTo = vi.fn(({ top }: ScrollToOptions) => {
      scrollTop = Number(top ?? 0);
    });
    scrollContainer.scrollTo = containerScrollTo as typeof scrollContainer.scrollTo;
    document.body.append(scrollContainer);

    render(
      <MemoryRouter initialEntries={["/"]}>
        <RouteScrollRestoration />
        <Link to="/tasks">tasks</Link>
        <Link to="/">首页</Link>
        <Routes>
          <Route path="/" element={<div>首页面</div>} />
          <Route path="/tasks" element={<div>任务中心页面</div>} />
        </Routes>
      </MemoryRouter>,
    );

    scrollTop = 750;
    scrollContainer.dispatchEvent(new Event("scroll"));
    fireEvent.click(screen.getByRole("link", { name: "tasks" }));
    containerScrollTo.mockClear();
    fireEvent.click(screen.getByRole("link", { name: "首页" }));

    await waitFor(
      () => {
        expect(containerScrollTo).toHaveBeenCalledWith({
          left: 0,
          top: 750,
          behavior: "auto",
        });
      },
      { timeout: 2000 },
    );
    expect(scrollToMock).not.toHaveBeenCalled();
  });

  it("保活路由之间切换时恢复滚动位置", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <RouteScrollRestoration />
        <Link to="/tasks">tasks</Link>
        <Link to="/">首页</Link>
        <Routes>
          <Route path="/" element={<div>首页面</div>} />
          <Route path="/tasks" element={<div>任务中心页面</div>} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(scrollToMock).not.toHaveBeenCalled();
    });

    // 模拟用户在 / 上滚到 200 —— scroll 事件实时写入 map
    simulateUserScroll(200);

    // 切到 tasks（保活路由）
    scrollToMock.mockClear();
    fireEvent.click(screen.getByRole("link", { name: "tasks" }));

    await waitFor(() => {
      expect(scrollToMock).toHaveBeenCalledWith({
        left: 0,
        top: 0,
        behavior: "auto",
      });
    });

    // 切回 '/'：双 rAF 预热 + 高度观察后异步触发
    scrollToMock.mockClear();
    fireEvent.click(screen.getByRole("link", { name: "首页" }));

    await waitFor(
      () => {
        expect(scrollToMock).toHaveBeenCalledWith({
          left: 0,
          top: 200,
          behavior: "auto",
        });
      },
      { timeout: 2000 },
    );
  });

  it("即使离开时 scrollY 被浏览器钳制为 0，仍能恢复到正确位置", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <RouteScrollRestoration />
        <Link to="/tasks">tasks</Link>
        <Link to="/">首页</Link>
        <Routes>
          <Route path="/" element={<div>首页面</div>} />
          <Route path="/tasks" element={<div>任务中心页面</div>} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(scrollToMock).not.toHaveBeenCalled();
    });

    simulateUserScroll(800);

    scrollToMock.mockClear();
    fireEvent.click(screen.getByRole("link", { name: "tasks" }));
    // 模拟钳制：scrollY 在路由切换的瞬间变成 0
    simulateUserScroll(0);

    await waitFor(() => {
      expect(scrollToMock).toHaveBeenCalledWith({
        left: 0,
        top: 0,
        behavior: "auto",
      });
    });

    // 切回 /：应该恢复到 800
    scrollToMock.mockClear();
    fireEvent.click(screen.getByRole("link", { name: "首页" }));

    await waitFor(
      () => {
        expect(scrollToMock).toHaveBeenCalledWith({
          left: 0,
          top: 800,
          behavior: "auto",
        });
      },
      { timeout: 2000 },
    );
  });

  it("切回后页面尚在加载、文档高度不足时，等到高度增长再恢复", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <RouteScrollRestoration />
        <Link to="/tasks">tasks</Link>
        <Link to="/">首页</Link>
        <Routes>
          <Route path="/" element={<div>首页面</div>} />
          <Route path="/tasks" element={<div>任务中心页面</div>} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(scrollToMock).not.toHaveBeenCalled();
    });

    simulateUserScroll(2000);

    // 切到 tasks
    fireEvent.click(screen.getByRole("link", { name: "tasks" }));
    simulateUserScroll(0);

    // 模拟切回 / 时页面还在加载：文档高度只够 600
    setDocHeight(600);
    scrollToMock.mockClear();
    fireEvent.click(screen.getByRole("link", { name: "首页" }));

    // 一开始不应该 scrollTo（高度不够）
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(scrollToMock).not.toHaveBeenCalledWith({
      left: 0,
      top: 2000,
      behavior: "auto",
    });

    // 模拟数据加载完，列表渲染出来，高度撑大
    setDocHeight(5000);

    // 下一帧观察器应该检测到并 scrollTo(2000)
    await waitFor(
      () => {
        expect(scrollToMock).toHaveBeenCalledWith({
          left: 0,
          top: 2000,
          behavior: "auto",
        });
      },
      { timeout: 2000 },
    );
  });

  it("首次进入没有记忆的保活路由时回到顶部", async () => {
    render(
      <MemoryRouter initialEntries={["/test-compose"]}>
        <RouteScrollRestoration />
        <Link to="/tasks">tasks</Link>
        <Routes>
          <Route path="/test-compose" element={<div>test-compose 页面</div>} />
          <Route path="/tasks" element={<div>任务中心页面</div>} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(scrollToMock).toHaveBeenCalled();
    });

    scrollToMock.mockClear();

    fireEvent.click(screen.getByRole("link", { name: "tasks" }));
    await waitFor(() => {
      expect(screen.getByText("任务中心页面")).toBeInTheDocument();
    });
    expect(scrollToMock).toHaveBeenCalledWith({
      left: 0,
      top: 0,
      behavior: "auto",
    });
  });

  it("保活路由内搜索参数变化不触发滚动", async () => {
    render(
      <MemoryRouter initialEntries={["/tasks"]}>
        <RouteScrollRestoration />
        <Link to="/tasks?status=running">running</Link>
        <Routes>
          <Route path="/tasks" element={<div>任务中心页面</div>} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(scrollToMock).not.toHaveBeenCalled();
    });

    scrollToMock.mockClear();
    fireEvent.click(screen.getByRole("link", { name: "running" }));
    await waitFor(() => {
      expect(screen.getByText("任务中心页面")).toBeInTheDocument();
    });
    expect(scrollToMock).not.toHaveBeenCalled();
  });

  it("任务中心发送计划与后台任务使用独立的滚动记忆", async () => {
    render(
      <MemoryRouter initialEntries={["/tasks?section=delivery"]}>
        <RouteScrollRestoration />
        <Link to="/">首页</Link>
        <Link to="/tasks?section=delivery">发送计划</Link>
        <Link to="/tasks?section=background">后台任务</Link>
        <Routes>
          <Route path="/" element={<div>首页面</div>} />
          <Route path="/tasks" element={<div>任务中心页面</div>} />
        </Routes>
      </MemoryRouter>,
    );

    simulateUserScroll(900);
    fireEvent.click(screen.getByRole("link", { name: "首页" }));

    scrollToMock.mockClear();
    fireEvent.click(screen.getByRole("link", { name: "后台任务" }));
    await waitFor(() => {
      expect(scrollToMock).toHaveBeenCalledWith({
        left: 0,
        top: 0,
        behavior: "auto",
      });
    });

    simulateUserScroll(300);
    fireEvent.click(screen.getByRole("link", { name: "首页" }));

    scrollToMock.mockClear();
    fireEvent.click(screen.getByRole("link", { name: "发送计划" }));
    await waitFor(
      () => {
        expect(scrollToMock).toHaveBeenCalledWith({
          left: 0,
          top: 900,
          behavior: "auto",
        });
      },
      { timeout: 2000 },
    );
  });

  it("恢复等待期间用户开始操作后不再抢回滚动位置", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <RouteScrollRestoration />
        <Link to="/tasks">tasks</Link>
        <Link to="/">首页</Link>
        <Routes>
          <Route path="/" element={<div>首页面</div>} />
          <Route path="/tasks" element={<div>任务中心页面</div>} />
        </Routes>
      </MemoryRouter>,
    );

    simulateUserScroll(2000);
    fireEvent.click(screen.getByRole("link", { name: "tasks" }));
    simulateUserScroll(0);
    setDocHeight(600);
    scrollToMock.mockClear();
    fireEvent.click(screen.getByRole("link", { name: "首页" }));

    window.dispatchEvent(new WheelEvent("wheel"));
    setDocHeight(5000);
    await new Promise((resolve) => setTimeout(resolve, 100));

    expect(scrollToMock).not.toHaveBeenCalledWith({
      left: 0,
      top: 2000,
      behavior: "auto",
    });
  });

  it("目标高度超时仍不可达时安全放弃而不是滚到页面底部", async () => {
    vi.useFakeTimers();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <RouteScrollRestoration />
        <Link to="/tasks">tasks</Link>
        <Link to="/">首页</Link>
        <Routes>
          <Route path="/" element={<div>首页面</div>} />
          <Route path="/tasks" element={<div>任务中心页面</div>} />
        </Routes>
      </MemoryRouter>,
    );

    simulateUserScroll(2000);
    fireEvent.click(screen.getByRole("link", { name: "tasks" }));
    simulateUserScroll(0);
    setDocHeight(1200);
    scrollToMock.mockClear();
    fireEvent.click(screen.getByRole("link", { name: "首页" }));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1800);
    });

    expect(scrollToMock).not.toHaveBeenCalledWith({
      left: 0,
      top: 400,
      behavior: "auto",
    });
    expect(scrollToMock).not.toHaveBeenCalledWith({
      left: 0,
      top: 2000,
      behavior: "auto",
    });
  });
});
