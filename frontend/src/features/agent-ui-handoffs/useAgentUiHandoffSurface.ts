import { useContext, useEffect, useRef } from 'react';
import type { DesktopAgentUiHandoffSurface } from '@/types/desktop';
import { AgentUiHandoffContext } from './context';
import type { AgentUiHandoffSurfaceHandler } from './types';

export const useAgentUiHandoffSurface = (
  surface: DesktopAgentUiHandoffSurface,
  handler: AgentUiHandoffSurfaceHandler,
) => {
  const context = useContext(AgentUiHandoffContext);
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(
    () => {
      if (context === null) {
        return undefined;
      }
      return context.registerSurfaceHandler(surface, (handoff) =>
        handlerRef.current(handoff),
      );
    },
    [context, surface],
  );
};

export const useActiveAgentUiHandoff = () => {
  const context = useContext(AgentUiHandoffContext);
  return context?.activeHandoff ?? null;
};
