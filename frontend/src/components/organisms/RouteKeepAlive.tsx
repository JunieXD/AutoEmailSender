import { Activity, useState, type ReactNode } from "react";

type CacheNode = {
  name: string;
  element: ReactNode;
  lastActiveTime: number;
};

type RouteKeepAliveState = {
  observedActiveName: string | null;
  cacheNodes: CacheNode[];
};

type RouteKeepAliveProps = {
  activeName: string | null;
  children: ReactNode;
  max: number;
};

const activateRoute = (
  state: RouteKeepAliveState,
  activeName: string | null,
  children: ReactNode,
  max: number,
): RouteKeepAliveState => {
  if (activeName === null) {
    return { ...state, observedActiveName: null };
  }

  const lastActiveTime = Date.now();
  const existing = state.cacheNodes.find((node) => node.name === activeName);
  if (existing) {
    return {
      observedActiveName: activeName,
      cacheNodes: state.cacheNodes.map((node) =>
        node.name === activeName
          ? { ...node, element: children, lastActiveTime }
          : node,
      ),
    };
  }

  const safeMax = Math.max(1, Math.trunc(max));
  const cacheNodes = [...state.cacheNodes];
  if (cacheNodes.length >= safeMax) {
    const victim = cacheNodes.reduce((oldest, node) =>
      node.lastActiveTime < oldest.lastActiveTime ? node : oldest,
    );
    cacheNodes.splice(cacheNodes.indexOf(victim), 1);
  }

  cacheNodes.push({ name: activeName, element: children, lastActiveTime });
  return { observedActiveName: activeName, cacheNodes };
};

/**
 * Router-specific Activity cache with an explicit inactive state.
 *
 * The active route is inserted through React's supported render-phase state adjustment pattern:
 * React immediately restarts this component before committing, so a newly visited route is present
 * in the same browser paint. This avoids the all-hidden frame caused by adding cache entries from a
 * deferred layout effect.
 */
export const RouteKeepAlive = ({
  activeName,
  children,
  max,
}: RouteKeepAliveProps) => {
  const [state, setState] = useState<RouteKeepAliveState>({
    observedActiveName: null,
    cacheNodes: [],
  });

  if (state.observedActiveName !== activeName) {
    setState((current) => activateRoute(current, activeName, children, max));
  }

  return (
    <div
      data-route-keep-alive="true"
      data-route-keep-alive-active={activeName !== null ? "true" : "false"}
      className={activeName === null ? "hidden" : "h-full min-h-0"}
    >
      {state.cacheNodes.map((node) => (
        <Activity
          key={node.name}
          mode={activeName === node.name ? "visible" : "hidden"}
        >
          {node.element}
        </Activity>
      ))}
    </div>
  );
};
