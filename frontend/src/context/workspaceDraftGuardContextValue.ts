import { createContext } from "react";

export type WorkspaceDraftGuardRequest = {
  nextPath?: string;
  nextIdentityId?: number | null;
  nextIdentityEditorId?: number | "new";
  nextLlmProfileId?: number | null;
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
