import path from "node:path";
import type { NativeImage } from "electron";

type WindowIconPathOptions = {
  isPackaged: boolean;
  platform: NodeJS.Platform;
  resourcesPath: string;
  repoRoot: string;
};

type TrayIconOptions = WindowIconPathOptions & {
  nativeImage: {
    createFromPath: (iconPath: string) => NativeImage;
  };
};

const MACOS_TRAY_ICON_SIZE = 18;

export function getWindowIconPath({
  isPackaged,
  platform,
  resourcesPath,
  repoRoot,
}: WindowIconPathOptions): string {
  const iconFileName = platform === "win32" ? "icon.ico" : "icon.png";
  return isPackaged
    ? path.join(resourcesPath, "build", iconFileName)
    : path.join(repoRoot, "desktop", "build", iconFileName);
}

export function createTrayIcon(options: TrayIconOptions): string | NativeImage {
  const iconPath = getWindowIconPath(options);
  if (options.platform !== "darwin") {
    return iconPath;
  }

  return options.nativeImage
    .createFromPath(iconPath)
    .resize({ width: MACOS_TRAY_ICON_SIZE, height: MACOS_TRAY_ICON_SIZE });
}
