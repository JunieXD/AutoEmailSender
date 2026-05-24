import { type MouseEvent, useCallback, useRef } from "react";

type DismissableLayerClickHandlers = {
  onBackdropClick: (event: MouseEvent<HTMLElement>) => void;
  onBackdropMouseDown: (event: MouseEvent<HTMLElement>) => void;
  onContentClick: (event: MouseEvent<HTMLElement>) => void;
  onContentMouseDown: () => void;
};

export const useDismissableLayerClick = (
  onDismiss: () => void,
): DismissableLayerClickHandlers => {
  const startedInsideContentRef = useRef(false);

  const onBackdropClick = useCallback(
    (event: MouseEvent<HTMLElement>) => {
      if (startedInsideContentRef.current) {
        startedInsideContentRef.current = false;
        return;
      }

      if (event.target === event.currentTarget) {
        onDismiss();
      }
    },
    [onDismiss],
  );

  const onContentClick = useCallback((event: MouseEvent<HTMLElement>) => {
    event.stopPropagation();
    startedInsideContentRef.current = false;
  }, []);

  const onBackdropMouseDown = useCallback((event: MouseEvent<HTMLElement>) => {
    if (event.target === event.currentTarget) {
      startedInsideContentRef.current = false;
    }
  }, []);

  const onContentMouseDown = useCallback(() => {
    startedInsideContentRef.current = true;
  }, []);

  return {
    onBackdropClick,
    onBackdropMouseDown,
    onContentClick,
    onContentMouseDown,
  };
};
