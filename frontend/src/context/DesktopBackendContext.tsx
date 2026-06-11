/* eslint-disable react-refresh/only-export-components */
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from "react";
import { updateDesktopBackendBaseUrl } from "@/lib/api/client";
import { recordDiagnosticEvent } from "@/lib/diagnostics";
import type { DesktopBackendStatus } from "@/types/desktop";

type DesktopBackendContextValue = {
  status: DesktopBackendStatus | null;
  isDesktop: boolean;
  isReady: boolean;
  disableReason: string | null;
};

const DesktopBackendContext = createContext<DesktopBackendContextValue | null>(null);

const initialStartingStatus: DesktopBackendStatus = {
  state: "starting",
  phase: "starting",
  message: "正在启动系统服务",
  elapsedSeconds: 0,
  slowStartup: false,
  verySlowStartup: false,
};

export const DesktopBackendProvider = ({ children }: PropsWithChildren) => {
  const isDesktop = Boolean(window.autoEmailSender);
  const [status, setStatus] = useState<DesktopBackendStatus | null>(
    () => (isDesktop ? initialStartingStatus : null),
  );

  useEffect(() => {
    const unsubscribe = window.autoEmailSender?.onBackendStatus?.((nextStatus) => {
      setStatus(nextStatus);
      updateDesktopBackendBaseUrl(
        nextStatus.state === "ready" ? nextStatus.baseUrl : null,
      );

      try {
        recordDiagnosticEvent({
          level: nextStatus.state === "error" ? "error" : "info",
          category: "system",
          eventName: `desktop.backend_${nextStatus.state}`,
          data: nextStatus,
        });
      } catch {
        // Diagnostics should never affect app startup.
      }
    });

    return () => {
      unsubscribe?.();
    };
  }, []);

  const value = useMemo<DesktopBackendContextValue>(() => {
    const isReady = !isDesktop || status?.state === "ready";
    return {
      status,
      isDesktop,
      isReady,
      disableReason: isReady ? null : "系统准备中",
    };
  }, [isDesktop, status]);

  return (
    <DesktopBackendContext.Provider value={value}>
      {children}
    </DesktopBackendContext.Provider>
  );
};

export const useDesktopBackend = (): DesktopBackendContextValue => {
  const context = useContext(DesktopBackendContext);
  if (context === null) {
    throw new Error("DesktopBackendContext 未初始化");
  }
  return context;
};
