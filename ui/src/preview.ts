// Loaded only by Vite's preview mode, before the app entry point.
import type { InvokeArgs } from "@tauri-apps/api/core";
import { emit } from "@tauri-apps/api/event";
import { mockIPC, mockWindows } from "@tauri-apps/api/mocks";
import type { CallError } from "./api/types";

declare global {
  interface ImportMetaEnv {
    readonly AES_PREVIEW_API: string;
    readonly AES_PREVIEW_DATA: string;
    readonly AES_PREVIEW_VERSION: string;
  }
}

const storageKey = `aes-preview-token:${import.meta.env.AES_PREVIEW_API}`;
const fragment = new URLSearchParams(location.hash.slice(1));
const incomingToken = fragment.get("preview-token");
if (incomingToken) {
  sessionStorage.setItem(storageKey, incomingToken);
  history.replaceState(null, "", location.pathname + location.search);
}
const token = sessionStorage.getItem(storageKey);

// The desktop app has one window, "main". The core cannot push events to a browser page; the only
// one delivered is raised by the call bridge below.
mockWindows("main");
mockIPC(invoke, { shouldMockEvents: true });

async function invoke(command: string, args?: InvokeArgs): Promise<unknown> {
  switch (command) {
    case "call": {
      if (!token) throw new Error("缺少预览令牌，请打开终端打印的完整网址。");
      const response = await fetch(`${import.meta.env.AES_PREVIEW_API}/call`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify(args),
      });
      if (response.ok) return response.json();
      const error: CallError = await response.json();
      // The desktop app hears this from the core when it clears the rejected token.
      if (error.code === "signed_out") void emit("account-changed");
      throw error;
    }
    case "startup_failure":
      return null;
    // Like the development build, which never looks for updates.
    case "update_status":
    case "check_update":
      return { kind: "current" };
    case "data_location":
      return import.meta.env.AES_PREVIEW_DATA;
    case "plugin:app|version":
      return import.meta.env.AES_PREVIEW_VERSION;
    case "plugin:window|set_theme":
      return undefined;
    case "plugin:opener|open_url": {
      if (!args || !("url" in args) || typeof args.url !== "string") throw new Error("链接无效。");
      const url = new URL(args.url);
      if (!["http:", "https:", "mailto:"].includes(url.protocol)) throw new Error("预览不支持打开这种链接。");
      window.open(url.href, "_blank", "noopener,noreferrer");
      return undefined;
    }
    default:
      throw new Error(`浏览器预览不支持此桌面功能：${command}。请在桌面开发版中操作。`);
  }
}
