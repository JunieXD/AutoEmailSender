type TrayLike = {
  on(eventName: "click" | "double-click" | "right-click", handler: () => void): void;
  popUpContextMenu(menu: unknown): void;
};

type TrayInteractionOptions = {
  openWindow: () => void;
  buildContextMenu: () => unknown;
  logEvent?: (eventName: string) => void;
};

export function bindTrayInteractions(
  tray: TrayLike,
  { openWindow, buildContextMenu, logEvent }: TrayInteractionOptions,
): void {
  tray.on("click", () => {
    logEvent?.("tray.click");
    openWindow();
  });
  tray.on("double-click", () => {
    logEvent?.("tray.double-click");
    openWindow();
  });
  tray.on("right-click", () => {
    logEvent?.("tray.right-click");
    tray.popUpContextMenu(buildContextMenu());
  });
}
