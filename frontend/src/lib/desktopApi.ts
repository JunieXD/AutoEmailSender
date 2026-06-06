import type { DesktopMaterialOpenResult, DesktopUpdateDownloadMode, DesktopUpdateStatus } from "@/types/desktop";

export const isDesktopApp = () => Boolean(window.autoEmailSender);

export async function openDesktopMaterial(materialId: number): Promise<DesktopMaterialOpenResult> {
  const api = getDesktopApi();
  if (!api.openMaterial) {
    return {
      ok: false,
      code: "MaterialOpenBackendUnavailable",
      message: "当前桌面应用版本不支持直接打开材料",
    };
  }
  return api.openMaterial({ materialId });
}
export async function quitDesktopApp(): Promise<void> {
  const api = getDesktopApi();
  if (!api.quitApp) {
    throw new Error("当前桌面应用版本不支持从窗口退出");
  }
  await api.quitApp();
}

export async function getDesktopAppVersion(): Promise<string> {
  const api = getDesktopApi();
  return api.getVersion();
}

export async function checkForDesktopUpdate() {
  const api = getDesktopApi();
  return api.checkForUpdate();
}

export async function downloadDesktopUpdate(mode: DesktopUpdateDownloadMode = "differential") {
  const api = getDesktopApi();
  return api.downloadUpdate({ mode });
}

export async function switchDesktopUpdateToFullDownload() {
  const api = getDesktopApi();
  return api.switchToFullDownload();
}

export async function installDownloadedDesktopUpdate(): Promise<void> {
  const api = getDesktopApi();
  await api.quitAndInstall();
}

export async function quitAndInstallDesktopUpdate(): Promise<void> {
  await installDownloadedDesktopUpdate();
}

export function onDesktopUpdateStatus(callback: (status: DesktopUpdateStatus) => void) {
  const api = window.autoEmailSender;
  if (!api) {
    return () => undefined;
  }
  return api.onUpdateStatus(callback);
}

function getDesktopApi(): NonNullable<typeof window.autoEmailSender> {
  if (!window.autoEmailSender) {
    throw new Error("当前不是桌面应用环境");
  }
  return window.autoEmailSender;
}

