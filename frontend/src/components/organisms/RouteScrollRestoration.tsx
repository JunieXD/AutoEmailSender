import { useCallback, useEffect, useLayoutEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import {
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

/**
 * 路由切换时处理 window 滚动位置。
 *
 * - 保活路由：通过 scroll 监听器**持续**记录 scrollY 到模块级 map。切回时启动一个
 *   有时限的"恢复观察器"：每帧检查 body 高度是否够 scrollTo 目标值，够了就还原。
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
 * 之前文档高度不够，scrollTo 会被钳到 maxScrollY。改成「监听文档高度，达标即恢复，
 * 超时即放弃」可以自适应不同页面的渲染速度，且不会与用户后续的手动滚动打架。
 */
export const RouteScrollRestoration = () => {
  const { pathname, search } = useLocation();
  // 当前激活的 pathname：scroll 监听器据此决定写到 map 的哪条 key。
  // 用 ref 而非 state，避免每次更新触发额外渲染。
  const currentPathnameRef = useRef<string | null>(null);
  const previousPathnameRef = useRef<string | null>(null);
  // 当前正在进行的恢复任务的 cancel 函数。
  const cancelRestoreRef = useRef<(() => void) | null>(null);

  const cancelPendingRestore = useCallback(() => {
    if (cancelRestoreRef.current) {
      cancelRestoreRef.current();
      cancelRestoreRef.current = null;
    }
  }, []);

  /**
   * 启动恢复观察器：等待 document 高度增长到能容纳 targetY 之后再 scrollTo。
   * 用 rAF 循环 + 超时窗口，达标或超时都会自动停止。
   * 期间用户的任何手动滚动都会通过 scroll 监听器写进 map，并不会被本恢复操作覆盖
   * —— 因为我们仅在尚未滚动到目标时才发 scrollTo，一旦达到/超过就停。
   */
  const scheduleRestore = useCallback(
    (targetY: number) => {
      cancelPendingRestore();
    const startTime = performance.now();
    let rafId: number | null = null;
    let cancelled = false;

    const tick = () => {
      if (cancelled) return;
      const docHeight = Math.max(
        document.documentElement.scrollHeight,
        document.body.scrollHeight,
      );
      const maxScrollY = docHeight - window.innerHeight;
      const elapsed = performance.now() - startTime;

      if (maxScrollY >= targetY) {
        // 文档高度够了，可以安全 scrollTo
        window.scrollTo({ left: 0, top: targetY, behavior: "auto" });
        cancelled = true;
        return;
      }

      if (elapsed >= SCROLL_RESTORE_WATCH_MS) {
        // 超时仍未达高度：尝试一次"尽力而为"的 scrollTo（会被钳制，但至少把
        // 滚动条推到能滚到的最远位置），然后放弃。
        if (maxScrollY > 0) {
          window.scrollTo({ left: 0, top: maxScrollY, behavior: "auto" });
        }
        cancelled = true;
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
  }, [cancelPendingRestore]);

  // 全局 scroll 监听：只在保活路由内写。监听器只挂一次（empty deps），整个会话存活。
  useEffect(() => {
    const handleScroll = () => {
      const path = currentPathnameRef.current;
      if (path && KEEP_ALIVE_PATHS.has(path)) {
        rememberKeepAliveScrollY(path, window.scrollY);
      }
    };
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  useLayoutEffect(() => {
    const previousPathname = previousPathnameRef.current;
    // 同步切换 currentPathnameRef：随后任何 scroll 事件（包括钳制触发的）都会被
    // 归到新路径，不再写到旧路径，保护离开前记录下来的真实 scrollY 不被覆盖。
    currentPathnameRef.current = pathname;

    cancelPendingRestore();

    if (KEEP_ALIVE_PATHS.has(pathname)) {
      if (previousPathname !== pathname) {
        const remembered = recallKeepAliveScrollY(pathname);
        if (remembered !== undefined && remembered > 0) {
          scheduleRestore(remembered);
        }
      }
      // pathname 未变（仅 search 变化）→ 页内交互，不操作 scroll。
    } else {
      // 非保活路由：切换即回到顶部。
      window.scrollTo({ left: 0, top: 0, behavior: "auto" });
    }

    previousPathnameRef.current = pathname;
  }, [pathname, search, cancelPendingRestore, scheduleRestore]);

  useEffect(() => cancelPendingRestore, [cancelPendingRestore]);

  return null;
};
