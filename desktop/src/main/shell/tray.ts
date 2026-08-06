type TrayLike = {
  on(eventName: "click" | "double-click", handler: () => void): void;
};

type TrayInteractionOptions = {
  openWindow: () => void;
};

export function bindTrayInteractions(tray: TrayLike, { openWindow }: TrayInteractionOptions): void {
  tray.on("click", openWindow);
  tray.on("double-click", openWindow);
}
