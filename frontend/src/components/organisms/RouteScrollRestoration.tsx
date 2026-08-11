import { useCallback, useEffect, useLayoutEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { getAppScrollContainer } from "@/lib/appScrollContainer";
import {
  getKeepAliveScrollKey,
  KEEP_ALIVE_PATHS,
  rememberKeepAliveScrollY,
  recallKeepAliveScrollY,
} from "@/lib/keepAliveRoutes";

/**
 * 切回保活路由后等待 DOM 高度增长的时间窗口（ms）。
 * 涵盖：Activity 切 visible + Transition 提交 + 数据请求 + 列表渲染。
 * 超过该窗口仍未达高度时停止重试，避免与用户的手动滚动打架。
 */
const SCROLL_RESTORE_WATCH_MS = 1500;
const SCROLL_INTENT_KEYS = new Set([
  "ArrowDown",
  "ArrowUp",
  "End",
  "Home",
  "PageDown",
  "PageUp",
  " ",
]);

const getScrollTop = () => getAppScrollContainer()?.scrollTop ?? window.scrollY;

const getMaxScrollTop = () => {
  const container = getAppScrollContainer();
  if (container) {
    return Math.max(0, container.scrollHeight - container.clientHeight);
  }
  const docHeight = Math.max(
    document.documentElement.scrollHeight,
    document.body.scrollHeight,
  );
  return Math.max(0, docHeight - window.innerHeight);
};

const scrollToPosition = (top: number) => {
  const options: ScrollToOptions = { left: 0, top, behavior: "auto" };
  const container = getAppScrollContainer();
  if (container) {
    container.scrollTo(options);
    return;
  }
  window.scrollTo(options);
};

/**
 * 路由切换时处理应用内容区的滚动位置（旧壳层下兼容 window）。
 *
 * - 保活路由：通过 scroll 监听器**持续**记录 scrollTop 到模块级 map。切回时启动一个
 *   有时限的"恢复观察器"：每帧检查滚动区域高度是否够目标值，够了就还原。
 *   覆盖 HomePage 这类切回后先显 skeleton/loading 再渲染列表的场景。
 * - 非保活路由：pathname 变化即 scrollTo(0,0)。
 *
 * # 为什么不在 useLayoutEffect 里直接读 scrollY 来记录
 *
 * 离开保活路由时，React 同步 commit 已经把当前页面的 <Activity> 切到 hidden
 * （display:none），文档高度急剧收缩；浏览器会**同步**把 window.scrollY 钳到新的
 * maxScrollY（通常是 0）。等 useLayoutEffect 跑到时，读到的是 0 而不是真实位置。
 * 改用全局 scroll 监听器持续记录，离开前最后一刻 map 里就是对的值。
 *
 * # 为什么 restore 用"等高度够再 scrollTo"而不是固定延迟
 *
 * HomePage 这类页面切回时：Activity visible → useEffect 重跑 → 显 skeleton/loading
 * → 拉数据 → 渲染列表，整个过程异步且耗时不定（取决于后端响应）。在列表渲染出来
 * 之前滚动区域高度不够，scrollTo 会被钳到最大值。改成「监听高度，达标即恢复，
 * 超时即安全放弃」可以自适应不同页面的渲染速度。恢复期间检测到滚轮、触摸、指针
 * 或滚动按键时会立即取消，用户操作始终优先。
 */
export const RouteScrollRestoration = () => {
  const { pathname, search } = useLocation();
  const scrollKey = getKeepAliveScrollKey(pathname, search);
  // 当前激活的滚动 key：scroll 监听器据此决定写到 map 的哪条记录。
  // 用 ref 而非 state，避免每次更新触发额外渲染。
  const currentScrollKeyRef = useRef<string | null>(null);
  const previousPathnameRef = useRef<string | null>(null);
  const previousScrollKeyRef = useRef<string | null>(null);
  // 当前正在进行的恢复任务的 cancel 函数。
  const cancelRestoreRef = useRef<(() => void) | null>(null);

  const cancelPendingRestore = useCallback(() => {
    if (cancelRestoreRef.current) {
      cancelRestoreRef.current();
      cancelRestoreRef.current = null;
    }
  }, []);

  /**
   * 启动恢复观察器：等待滚动区域高度增长到能容纳 targetY 之后再 scrollTo。
   * 用 rAF 循环 + 超时窗口，达标或超时都会自动停止。
   * 只有在目标位置确实可达时才恢复；超时不会猜测一个替代位置。任何明确的用户滚动
   * 意图都会从外层监听器调用 cancelPendingRestore，避免延迟恢复抢走控制权。
   */
  const scheduleRestore = useCallback(
    (targetY: number) => {
      cancelPendingRestore();
      const startTime = performance.now();
      let rafId: number | null = null;
      let cancelled = false;

      const tick = () => {
        if (cancelled) return;
        const maxScrollY = getMaxScrollTop();
        const elapsed = performance.now() - startTime;

        if (maxScrollY >= targetY) {
          // 滚动区域高度够了，可以安全恢复。
          scrollToPosition(targetY);
          cancelled = true;
          cancelRestoreRef.current = null;
          return;
        }

        if (elapsed >= SCROLL_RESTORE_WATCH_MS) {
          // 目标仍不可达时安全放弃。滚到 maxScrollY 会把用户意外送到页面底部，并且
          // 在异步内容继续增长后留下一个没有业务含义的位置。
          cancelled = true;
          cancelRestoreRef.current = null;
          return;
        }

        rafId = requestAnimationFrame(tick);
      };

      // 先用两个 rAF 让 Activity Transition 完成提交、layout 稳定，再进入观察循环。
      let warmupRaf1: number | null = null;
      let warmupRaf2: number | null = null;
      warmupRaf1 = requestAnimationFrame(() => {
        if (cancelled) return;
        warmupRaf2 = requestAnimationFrame(() => {
          if (cancelled) return;
          tick();
        });
      });

      cancelRestoreRef.current = () => {
        cancelled = true;
        if (warmupRaf1 !== null) cancelAnimationFrame(warmupRaf1);
        if (warmupRaf2 !== null) cancelAnimationFrame(warmupRaf2);
        if (rafId !== null) cancelAnimationFrame(rafId);
      };
    },
    [cancelPendingRestore],
  );

  // 全局 scroll 监听：只在保活路由内写。监听器只挂一次（empty deps），整个会话存活。
  useEffect(() => {
    const handleScroll = () => {
      const key = currentScrollKeyRef.current;
      if (key) {
        rememberKeepAliveScrollY(key, getScrollTop());
      }
    };
    const scrollTarget = getAppScrollContainer() ?? window;
    scrollTarget.addEventListener("scroll", handleScroll, { passive: true });
    return () => scrollTarget.removeEventListener("scroll", handleScroll);
  }, []);

  useEffect(() => {
    const cancelForUserIntent = () => cancelPendingRestore();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (SCROLL_INTENT_KEYS.has(event.key)) {
        cancelForUserIntent();
      }
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        cancelPendingRestore();
      }
    };

    window.addEventListener("wheel", cancelForUserIntent, { passive: true });
    window.addEventListener("touchstart", cancelForUserIntent, { passive: true });
    window.addEventListener("pointerdown", cancelForUserIntent, { passive: true });
    window.addEventListener("keydown", handleKeyDown);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      window.removeEventListener("wheel", cancelForUserIntent);
      window.removeEventListener("touchstart", cancelForUserIntent);
      window.removeEventListener("pointerdown", cancelForUserIntent);
      window.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [cancelPendingRestore]);

  useLayoutEffect(() => {
    const previousPathname = previousPathnameRef.current;
    const previousScrollKey = previousScrollKeyRef.current;
    // 同步切换 currentScrollKeyRef：随后任何 scroll 事件（包括钳制触发的）都会被
    // 归到新路径，不再写到旧路径，保护离开前记录下来的真实 scrollY 不被覆盖。
    currentScrollKeyRef.current = KEEP_ALIVE_PATHS.has(pathname) ? scrollKey : null;

    cancelPendingRestore();

    if (KEEP_ALIVE_PATHS.has(pathname)) {
      if (previousPathname !== null && previousScrollKey !== scrollKey) {
        const remembered = recallKeepAliveScrollY(scrollKey);
        if (remembered !== undefined && remembered > 0) {
          scheduleRestore(remembered);
        } else {
          scrollToPosition(0);
        }
      }
      // 同一个滚动 key 内的搜索参数变化属于页内交互，不操作滚动。
    } else {
      // 非保活路由：切换即回到顶部。
      scrollToPosition(0);
    }

    previousPathnameRef.current = pathname;
    previousScrollKeyRef.current = KEEP_ALIVE_PATHS.has(pathname) ? scrollKey : null;
  }, [pathname, scrollKey, cancelPendingRestore, scheduleRestore]);

  useEffect(() => cancelPendingRestore, [cancelPendingRestore]);

  return null;
};
