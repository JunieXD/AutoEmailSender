import { createContext } from 'react';
import type { DesktopAgentUiHandoffSurface } from '@/types/desktop';
import type {
  AgentUiHandoffSurfaceHandler,
  ValidatedAgentUiHandoff,
} from './types';

export type AgentUiHandoffContextValue = {
  activeHandoff: ValidatedAgentUiHandoff | null;
  registerSurfaceHandler: (
    surface: DesktopAgentUiHandoffSurface,
    handler: AgentUiHandoffSurfaceHandler,
  ) => () => void;
};

export const AgentUiHandoffContext =
  createContext<AgentUiHandoffContextValue | null>(null);
