type WindowCloseState = {
  isPackaged: boolean;
  isQuitting: boolean;
  platform: NodeJS.Platform;
};

type WindowCreationState = {
  pendingCreation: Promise<void> | null;
};

type RestorableWindow = {
  isMinimized: () => boolean;
  restore: () => void;
  show: () => void;
  focus: () => void;
};

export function shouldHideWindowOnClose({
  isPackaged,
  isQuitting,
  platform,
}: WindowCloseState): boolean {
  if (isQuitting) {
    return false;
  }
  return isPackaged || platform !== "linux";
}

export function restoreExistingWindow(window: RestorableWindow): void {
  if (window.isMinimized()) {
    window.restore();
  }
  window.show();
  window.focus();
}

export function startWindowCreationOnce(
  state: WindowCreationState,
  createWindow: () => Promise<void>,
): Promise<void> {
  if (state.pendingCreation !== null) {
    return state.pendingCreation;
  }

  state.pendingCreation = createWindow().finally(() => {
    state.pendingCreation = null;
  });
  return state.pendingCreation;
}
