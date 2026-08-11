import { useOutlet, useLocation } from "react-router-dom";
import { RouteKeepAlive } from "@/components/organisms/RouteKeepAlive";
import { KEEP_ALIVE_PATHS } from "@/lib/keepAliveRoutes";

/**
 * 路由级保活布局。AppShell 下**所有**路由都挂在此布局之下，由它自己根据当前 pathname
 * 决定渲染策略：
 *
 * - 命中 `KEEP_ALIVE_PATHS` 白名单 → 将 outlet 放入 <KeepAlive>，复用/新建缓存节点；
 * - 否则 → 旁路渲染 outlet，<RouteKeepAlive> 仍然挂载并进入显式 inactive 状态，
 *   保留已有缓存但不缓存非保活页面。
 *
 * 这样无论用户在哪个路由之间跳转，<KeepAlive> 始终是同一棵 React 树的同一个实例，
 * 其内部 useState<CacheNode[]> 永不丢失——这是"保活页面 → 非保活页面 → 回到保活页面
 * 仍能恢复"的关键。
 *
 * 用 `useOutlet()` 而非 `<Outlet/>`：`useOutlet` 拿到的是当前匹配子路由的 React element，
 * 可以作为 children 显式传给 KeepAlive 接管。
 */
export const KeepAliveLayout = () => {
  const outlet = useOutlet();
  const { pathname } = useLocation();
  const isKeepAlive = KEEP_ALIVE_PATHS.has(pathname);

  return (
    <>
      <RouteKeepAlive
        activeName={isKeepAlive ? pathname : null}
        max={KEEP_ALIVE_PATHS.size}
      >
        {isKeepAlive ? outlet : null}
      </RouteKeepAlive>
      {isKeepAlive ? null : outlet}
    </>
  );
};
