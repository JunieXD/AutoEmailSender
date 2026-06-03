import { useContext } from "react";
import { WorkspaceDraftGuardContext } from "@/context/workspaceDraftGuardContextValue";

export const useWorkspaceDraftGuard = () => {
  const context = useContext(WorkspaceDraftGuardContext);
  if (context === null) {
    throw new Error("WorkspaceDraftGuardContext 未初始化");
  }
  return context;
};