import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  type PropsWithChildren,
} from "react";

type WorkspaceDraftGuard = () => Promise<boolean>;

type WorkspaceDraftGuardContextValue = {
  requestWorkspaceDraftGuard: () => Promise<boolean>;
  registerWorkspaceDraftGuard: (guard: WorkspaceDraftGuard) => () => void;
};

const WorkspaceDraftGuardContext = createContext<WorkspaceDraftGuardContextValue | null>(null);

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

  const requestWorkspaceDraftGuard = useCallback(() => {
    if (!guardRef.current) {
      return Promise.resolve(true);
    }
    if (pendingGuardRef.current) {
      return pendingGuardRef.current;
    }
    const request = guardRef.current().finally(() => {
      if (pendingGuardRef.current === request) {
        pendingGuardRef.current = null;
      }
    });
    pendingGuardRef.current = request;
    return request;
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

export const useWorkspaceDraftGuard = () => {
  const context = useContext(WorkspaceDraftGuardContext);
  if (context === null) {
    throw new Error("WorkspaceDraftGuardContext 未初始化");
  }
  return context;
};
