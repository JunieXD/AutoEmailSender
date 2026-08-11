/**
 * 路由级保活配置。
 *
 * - `KEEP_ALIVE_PATHS`：启用 <Activity> 保活的精确路径白名单。只缓存用户会反复
 *   横跳查看、筛选、分页、选中或编辑配置的工作台页面；创建/测试/工作区详情等
 *   一次性流程继续按普通路由渲染，避免保留过期编辑上下文。
 *   `KeepAliveLayout` 只挂载这些路由，`RouteScrollRestoration` 用它判断是否需要记忆/恢复滚动位置。
 * - `keepAliveScrollMemory`：window 滚动位置的模块级缓存——保活只保留 React state，
 *   文档滚动条不属于组件状态，需要单独兜底。
 */
const PATHS = ["/", "/dashboard", "/professors", "/tasks", "/profile"] as const;

export const KEEP_ALIVE_PATHS: ReadonlySet<string> = new Set(PATHS);

const scrollMemory = new Map<string, number>();

export const getKeepAliveScrollKey = (pathname: string, search = "") => {
  if (pathname !== "/tasks") {
    return pathname;
  }

  const section = new URLSearchParams(search).get("section") === "background"
    ? "background"
    : "delivery";
  return `${pathname}?section=${section}`;
};

export const rememberKeepAliveScrollY = (key: string, scrollY: number) => {
  scrollMemory.set(key, scrollY);
};

export const recallKeepAliveScrollY = (key: string): number | undefined =>
  scrollMemory.get(key);

/** 仅供测试使用：清空模块级 scroll 缓存，避免用例之间相互污染。 */
export const __resetKeepAliveScrollMemory = () => {
  scrollMemory.clear();
};
