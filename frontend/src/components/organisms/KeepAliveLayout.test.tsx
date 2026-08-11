import { useEffect, useState } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import {
  Link,
  MemoryRouter,
  Route,
  Routes,
} from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { KEEP_ALIVE_PATHS } from "@/lib/keepAliveRoutes";
import { KeepAliveLayout } from "@/components/organisms/KeepAliveLayout";

/**
 * 计数器：每次 mount/unmount 都会通过 props 上报，便于检测 Activity 是否
 * 在切走时调用 effect cleanup（按设计：会 cleanup），切回时是否复用了原 useState。
 */
const Counter = ({
  label,
  onMount,
  onUnmount,
}: {
  label: string;
  onMount: () => void;
  onUnmount: () => void;
}) => {
  const [count, setCount] = useState(0);

  useEffect(() => {
    onMount();
    return () => {
      onUnmount();
    };
  }, [onMount, onUnmount]);

  return (
    <div>
      <span data-testid={`${label}-count`}>{count}</span>
      <button type="button" onClick={() => setCount((value) => value + 1)}>
        increment-{label}
      </button>
    </div>
  );
};

describe("KeepAliveLayout", () => {
  it("保留保活子路由的 React state，并暴露白名单常量", () => {
    expect(KEEP_ALIVE_PATHS.has("/")).toBe(true);
    expect(KEEP_ALIVE_PATHS.has("/dashboard")).toBe(true);
    expect(KEEP_ALIVE_PATHS.has("/professors")).toBe(true);
    expect(KEEP_ALIVE_PATHS.has("/tasks")).toBe(true);
    expect(KEEP_ALIVE_PATHS.has("/profile")).toBe(true);
    expect(KEEP_ALIVE_PATHS.size).toBe(5);

    const homeMount = vi.fn();
    const homeUnmount = vi.fn();
    const dashboardMount = vi.fn();
    const dashboardUnmount = vi.fn();

    render(
      <MemoryRouter initialEntries={["/"]}>
        <Link to="/">home</Link>
        <Link to="/dashboard">dashboard</Link>
        <Routes>
          <Route element={<KeepAliveLayout />}>
            <Route
              index
              element={<Counter label="home" onMount={homeMount} onUnmount={homeUnmount} />}
            />
            <Route
              path="dashboard"
              element={
                <Counter
                  label="dashboard"
                  onMount={dashboardMount}
                  onUnmount={dashboardUnmount}
                />
              }
            />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    // 初次渲染：home 上线
    expect(screen.getByTestId("home-count").textContent).toBe("0");
    expect(homeMount).toHaveBeenCalledTimes(1);

    // 在 home 上点 3 次
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "increment-home" }));
      fireEvent.click(screen.getByRole("button", { name: "increment-home" }));
      fireEvent.click(screen.getByRole("button", { name: "increment-home" }));
    });
    expect(screen.getByTestId("home-count").textContent).toBe("3");

    // 切到 dashboard
    act(() => {
      fireEvent.click(screen.getByRole("link", { name: "dashboard" }));
    });
    // dashboard 首次挂载
    expect(dashboardMount).toHaveBeenCalled();
    // home 被隐藏：按 Activity 语义会 cleanup effect（≠ React unmount，但我们用 useEffect 的
    // cleanup 来观察）
    expect(homeUnmount).toHaveBeenCalledTimes(1);

    // 再切回 home：state 必须保留为 3，证明 KeepAlive 真的缓存了节点
    act(() => {
      fireEvent.click(screen.getByRole("link", { name: "home" }));
    });
    expect(screen.getByTestId("home-count").textContent).toBe("3");
    // 切回后 effect 重新建立，但这是同一份 state（mount 次数 >= 2）
    expect(homeMount.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("子节点为 null 时不抛错（路由无匹配）", () => {
    render(
      <MemoryRouter initialEntries={["/never"]}>
        <Routes>
          <Route element={<KeepAliveLayout />}>
            <Route path="other" element={<div>other</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    // 没有匹配的 outlet，但 KeepAliveLayout 自身不应崩溃
    expect(screen.queryByText("other")).toBeNull();
  });

  it("途经非保活路由再回到保活路由时仍能复用缓存的 state", () => {
    const homeMount = vi.fn();
    const homeUnmount = vi.fn();

    render(
      <MemoryRouter initialEntries={["/"]}>
        <Link to="/">home</Link>
        <Link to="/create-task">create-task</Link>
        <Routes>
          <Route element={<KeepAliveLayout />}>
            <Route
              index
              element={<Counter label="home" onMount={homeMount} onUnmount={homeUnmount} />}
            />
            {/* 非保活路由：路径不在 KEEP_ALIVE_PATHS 白名单中 */}
            <Route path="create-task" element={<div>create-task 页面</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    // 在保活的 home 上累加到 5
    act(() => {
      for (let i = 0; i < 5; i += 1) {
        fireEvent.click(screen.getByRole("button", { name: "increment-home" }));
      }
    });
    expect(screen.getByTestId("home-count").textContent).toBe("5");

    // 切到非保活路由：home 的 effect cleanup 触发；React state 仍由 KeepAlive 持有
    act(() => {
      fireEvent.click(screen.getByRole("link", { name: "create-task" }));
    });
    expect(screen.getByText("create-task 页面")).toBeInTheDocument();
    expect(document.querySelector('[data-route-keep-alive="true"]')).toHaveClass(
      "hidden",
    );
    expect(
      document.querySelector('[data-route-keep-alive="true"]'),
    ).toHaveAttribute("data-route-keep-alive-active", "false");

    // 切回 home：必须复用之前那份缓存（count 仍是 5），而不是从 0 开始
    act(() => {
      fireEvent.click(screen.getByRole("link", { name: "home" }));
    });
    expect(screen.getByTestId("home-count").textContent).toBe("5");
    expect(document.querySelector('[data-route-keep-alive="true"]')).not.toHaveClass(
      "hidden",
    );
    expect(
      document.querySelector('[data-route-keep-alive="true"]'),
    ).toHaveAttribute("data-route-keep-alive-active", "true");
  });
});
