import { createContext } from "react";

export type WorkspaceDraftGuard = () => Promise<boolean>;

export type WorkspaceDraftGuardContextValue = {
  requestWorkspaceDraftGuard: () => Promise<boolean>;
  registerWorkspaceDraftGuard: (guard: WorkspaceDraftGuard) => () => void;
};

export const WorkspaceDraftGuardContext =
  createContext<WorkspaceDraftGuardContextValue | null>(null);