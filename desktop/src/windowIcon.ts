import path from "node:path";

type WindowIconPathOptions = {
  isPackaged: boolean;
  platform: NodeJS.Platform;
  resourcesPath: string;
  repoRoot: string;
};

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
