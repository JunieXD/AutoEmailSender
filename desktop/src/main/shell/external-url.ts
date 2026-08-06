import { BrowserWindow, ipcMain, shell } from "electron";
import { access } from "node:fs/promises";
import path from "node:path";
import { DESKTOP_IPC_CHANNELS } from "../../contracts/channels.js";

export const EXTERNAL_URL_OPEN_IPC_CHANNEL = DESKTOP_IPC_CHANNELS.externalUrlOpen;

type ExternalUrlDependencies = {
  openExternal: (url: string) => Promise<void>;
  openElectronWindow: (url: string) => void;
  shouldUseSystemExternalOpener: () => Promise<boolean> | boolean;
};

type ExternalUrlServiceOptions = {
  dependencies?: Partial<ExternalUrlDependencies>;
};

const defaultDependencies: ExternalUrlDependencies = {
  openExternal: (url: string) => shell.openExternal(url),
  openElectronWindow: (url: string) => {
    const externalWindow = new BrowserWindow({
      width: 1200,
      height: 820,
      autoHideMenuBar: true,
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
      },
    });
    externalWindow.webContents.setWindowOpenHandler(({ url: nextUrl }) => {
      const parsedUrl = parseWebUrl(nextUrl);
      return parsedUrl === null ? { action: "deny" } : { action: "allow" };
    });
    void externalWindow.loadURL(url);
  },
  shouldUseSystemExternalOpener,
};

export function createExternalUrlService(options: ExternalUrlServiceOptions = {}) {
  const dependencies = { ...defaultDependencies, ...options.dependencies };

  return {
    async openExternalUrl(url: unknown): Promise<void> {
      const parsedUrl = parseWebUrl(url);
      if (parsedUrl === null) {
        throw new Error("Only http and https URLs can be opened externally.");
      }

      if (!(await dependencies.shouldUseSystemExternalOpener())) {
        dependencies.openElectronWindow(parsedUrl);
        return;
      }

      try {
        await dependencies.openExternal(parsedUrl);
      } catch {
        dependencies.openElectronWindow(parsedUrl);
      }
    },
  };
}

export function registerExternalUrlIpc(): void {
  const service = createExternalUrlService();
  ipcMain.handle(EXTERNAL_URL_OPEN_IPC_CHANNEL, (_event, url: unknown) =>
    service.openExternalUrl(url),
  );
}

function parseWebUrl(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }

  try {
    const parsedUrl = new URL(value);
    if (parsedUrl.protocol !== "http:" && parsedUrl.protocol !== "https:") {
      return null;
    }
    return parsedUrl.toString();
  } catch {
    return null;
  }
}

async function shouldUseSystemExternalOpener(): Promise<boolean> {
  if (process.platform !== "linux") {
    return true;
  }

  const pathEntries = (process.env.PATH ?? "")
    .split(path.delimiter)
    .filter(Boolean);

  for (const entry of pathEntries) {
    try {
      await access(path.join(entry, "xdg-open"));
      return true;
    } catch {
      // Continue checking the remaining PATH entries.
    }
  }

  return false;
}
