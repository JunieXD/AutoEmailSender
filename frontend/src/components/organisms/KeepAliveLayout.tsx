import type { ComponentProps, ComponentType } from "react";
import { useOutlet, useLocation } from "react-router-dom";
import KeepAlive from "react-activity-keepalive-kit";
import { KEEP_ALIVE_PATHS } from "@/lib/keepAliveRoutes";

/**
 * 库的 TS 签名要求 `activeName: string`，但其内部 useLayoutEffect 首行明确处理
 * `if (activeName == null) return;` 的早返回路径（不写入也不更新缓存）。我们利用这点：
 * 非保活路由匹配时传入 null，让 KeepAlive 保持挂载、保留已有缓存节点（所有节点的
 * Activity 进入 hidden 状态），但不会把"非保活页面"误写进缓存。
 */
type PausableKeepAliveProps = Omit<ComponentProps<typeof KeepAlive>, "activeName"> & {
  activeName: string | null;
};
const PausableKeepAlive = KeepAlive as unknown as ComponentType<PausableKeepAliveProps>;

/**
 * 路由级保活布局。AppShell 下**所有**路由都挂在此布局之下，由它自己根据当前 pathname
 * 决定渲染策略：
 *
 * - 命中 `KEEP_ALIVE_PATHS` 白名单 → 将 outlet 放入 <KeepAlive>，复用/新建缓存节点；
 * - 否则 → 旁路渲染 outlet，<KeepAlive> 仍然挂载在树上以保留已有缓存（activeName=null
 *   触发库内部早返回，避免污染缓存）。
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
      <PausableKeepAlive
        activeName={isKeepAlive ? pathname : null}
        max={KEEP_ALIVE_PATHS.size}
        strategy="LRU"
      >
        {isKeepAlive ? outlet : null}
      </PausableKeepAlive>
      {isKeepAlive ? null : outlet}
    </>
  );
};
