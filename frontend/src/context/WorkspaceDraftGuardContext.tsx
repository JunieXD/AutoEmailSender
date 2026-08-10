import { useCallback, useMemo, useRef, type PropsWithChildren } from "react";

import {
  WorkspaceDraftGuardContext,
  type WorkspaceDraftGuard,
} from "@/context/workspaceDraftGuardContextValue";

export const WorkspaceDraftGuardProvider = ({ children }: PropsWithChildren) => {
  const guardRef = useRef<WorkspaceDraftGuard | null>(null);
  const pendingGuardRef = useRef<Promise<boolean> | null>(null);

  const registerWorkspaceDraftGuard = useCallback((guard: WorkspaceDraftGuard) => {
    guardRef.current = guard;
    return () => {
      if (guardRef.current === guard) {
        guardRef.current = null;
      }
    };
  }, []);

  const requestWorkspaceDraftGuard = useCallback((request?: Parameters<WorkspaceDraftGuard>[0]) => {
    if (!guardRef.current) {
      return Promise.resolve(true);
    }
    if (pendingGuardRef.current) {
      return pendingGuardRef.current;
    }
    const pendingRequest = guardRef.current(request).finally(() => {
      if (pendingGuardRef.current === pendingRequest) {
        pendingGuardRef.current = null;
      }
    });
    pendingGuardRef.current = pendingRequest;
    return pendingRequest;
  }, []);

  const value = useMemo(
    () => ({
      requestWorkspaceDraftGuard,
      registerWorkspaceDraftGuard,
    }),
    [registerWorkspaceDraftGuard, requestWorkspaceDraftGuard],
  );

  return (
    <WorkspaceDraftGuardContext.Provider value={value}>
      {children}
    </WorkspaceDraftGuardContext.Provider>
  );
};
