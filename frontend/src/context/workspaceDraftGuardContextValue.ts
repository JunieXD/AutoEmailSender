import { createContext } from "react";

export type WorkspaceDraftGuardRequest = {
  nextPath?: string;
};

export type WorkspaceDraftGuard = (
  request?: WorkspaceDraftGuardRequest,
) => Promise<boolean>;

export type WorkspaceDraftGuardContextValue = {
  requestWorkspaceDraftGuard: (
    request?: WorkspaceDraftGuardRequest,
  ) => Promise<boolean>;
  registerWorkspaceDraftGuard: (guard: WorkspaceDraftGuard) => () => void;
};

export const WorkspaceDraftGuardContext =
  createContext<WorkspaceDraftGuardContextValue | null>(null);
