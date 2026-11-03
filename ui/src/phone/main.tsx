// The page on a phone (phone.html, docs/development.md#手机远程): the app as on the computer, with
// its calls and events going through the session with it, the way the browser preview stands in for
// the desktop app (src/preview.ts).
import type { InvokeArgs } from "@tauri-apps/api/core";
import { emit } from "@tauri-apps/api/event";
import { mockIPC, mockWindows } from "@tauri-apps/api/mocks";
import { StrictMode, useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import appIcon from "../../../docs/icon.svg";
import { App } from "@/app";
import { Button } from "@/components/ui/button";
import { queryClient } from "@/query-client";
import "@/index.css";
import { followTheme } from "@/theme";
import { forget, named, type Pairing, pairings, Unreadable } from "./pairings";
import { CLOSE, Connection, DISCONNECTED, type Status } from "./session";

declare global {
  interface ImportMetaEnv {
    readonly AES_PHONE_VERSION: string;
  }
}

/** The loader, which picks the computer and the page of its app's version. */
const LOADER = "/m/";
/** After a dropped connection, try again after 1, 2, 4… seconds, at most this long. */
const LONGEST_WAIT = 10_000;
/** How often to make sure the connection still works while the page is in front. */
const CHECK_EVERY = 30_000;

type View =
  | { kind: "connecting" }
  /** Trying again, before the app came up. */
  | { kind: "waiting"; message: string }
  | { kind: "ended"; message: string }
  /** The app is up; `connected` is false while it reconnects. */
  | { kind: "ready"; connected: boolean };

class Link {
  view: View = { kind: "connecting" };
  private connection: Connection | null = null;
  private retry: ReturnType<typeof setTimeout> | undefined;
  private wait = 1000;
  private readonly listeners = new Set<() => void>();

  constructor(private pairing: Pairing) {
    // Phones drop the connections of pages in the background, or keep ones the network lost
    // meanwhile: back in front, reconnect at once, or make sure the connection still works.
    const back = () => {
      if (document.visibilityState !== "visible" || this.view.kind === "ended") return;
      if (this.connection === null) this.connect();
      else this.connection.check();
    };
    document.addEventListener("visibilitychange", back);
    window.addEventListener("online", back);
    // A connection lost while the page is in front says nothing either.
    setInterval(() => {
      if (document.visibilityState === "visible") this.connection?.check();
    }, CHECK_EVERY);
  }

  readonly subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private set(view: View) {
    this.view = view;
    for (const listener of this.listeners) listener();
  }

  connect() {
    clearTimeout(this.retry);
    if (this.view.kind !== "ready") this.set({ kind: "connecting" });
    this.connection = new Connection(
      this.pairing,
      (status) => this.changed(status),
      (event, payload) => void emit(event, payload),
    );
  }

  call(op: string, input: unknown): Promise<unknown> {
    return this.connection ? this.connection.call(op, input) : Promise.reject(DISCONNECTED);
  }

  private changed(status: Status) {
    const { room } = this.pairing;
    if (status.kind === "ready") {
      // The computer's app was updated since the page loaded.
      if (status.welcome.version !== import.meta.env.AES_PHONE_VERSION) {
        location.replace(`${LOADER}?c=${room}`);
        return;
      }
      this.pairing = { ...this.pairing, computer: status.welcome.computer };
      this.wait = 1000;
      const again = this.view.kind === "ready";
      this.set({ kind: "ready", connected: true });
      // Things may have changed meanwhile.
      if (again) void queryClient.invalidateQueries();
      // The browser's storage last, here and below, so that a browser refusing it holds up nothing
      // on screen.
      named(room, status.welcome.computer);
      return;
    }
    this.connection = null;
    switch (status.code) {
      case CLOSE.UNPAIRED:
        this.set({
          kind: "ended",
          message:
            this.pairing.computer === null
              ? "二维码已过期或已被用过，请回到电脑上重新添加这部手机。"
              : "这部手机已在电脑上撤销，请重新扫码配对。",
        });
        return forget(room);
      case CLOSE.DENIED:
        this.set({ kind: "ended", message: "电脑上没有允许这部手机连接，请回到电脑上重新添加。" });
        return forget(room);
      case CLOSE.BROKEN:
        return this.set({
          kind: "ended",
          message: "和电脑的连接出错了，请刷新页面再试；仍然出错时，在电脑上导出日志发给开发者。",
        });
    }
    const offline = status.code === CLOSE.OFFLINE;
    this.retry = setTimeout(() => this.connect(), offline ? LONGEST_WAIT : this.wait);
    this.wait = Math.min(this.wait * 2, LONGEST_WAIT);
    if (this.view.kind === "ready") return this.set({ kind: "ready", connected: false });
    this.set({
      kind: "waiting",
      message: offline
        ? "电脑上的应用没有连上：请确认它开着、已登录、能联网。"
        : status.code === CLOSE.FULL
          ? "这台电脑同时连着的手机太多了，稍后自动再试。"
          : "连不上服务器，正在重试…",
    });
  }
}

function Phone({ link, computer }: { link: Link; computer: string | null }) {
  const view = useSyncExternalStore(link.subscribe, () => link.view);
  if (view.kind === "ready") {
    return (
      <>
        {!view.connected && (
          <div
            role="status"
            className="fixed inset-x-0 top-0 z-50 bg-warning-soft px-4 py-2 text-center text-sm text-warning"
          >
            连接断开，正在重连…
          </div>
        )}
        <App />
      </>
    );
  }
  return (
    <div className="grid h-dvh place-items-center p-6">
      <div className="flex max-w-[320px] flex-col items-center gap-4 text-center">
        <img src={appIcon} alt="" className="size-12" />
        {view.kind === "connecting" ? (
          <p>{computer === null ? "请在电脑上允许这台手机连接。" : `正在连接 ${computer}…`}</p>
        ) : (
          <p>{view.message}</p>
        )}
        {view.kind === "waiting" && (
          <Button variant="quiet" onClick={() => link.connect()}>
            重试
          </Button>
        )}
      </div>
    </div>
  );
}

async function invoke(link: Link, command: string, args?: InvokeArgs): Promise<unknown> {
  switch (command) {
    case "call":
      if (!args || !("op" in args) || typeof args.op !== "string") throw new Error("调用无效。");
      return link.call(args.op, "input" in args ? args.input : undefined);
    case "startup_failure":
      return null;
    case "plugin:app|version":
      return import.meta.env.AES_PHONE_VERSION;
    case "plugin:window|set_theme":
      return undefined;
    case "plugin:opener|open_url": {
      if (!args || !("url" in args) || typeof args.url !== "string") throw new Error("链接无效。");
      const url = new URL(args.url);
      if (!["http:", "https:", "mailto:"].includes(url.protocol)) throw new Error("手机上打不开这种链接。");
      window.open(url.href, "_blank", "noopener,noreferrer");
      return undefined;
    }
    default:
      throw new Error("这个操作只能在电脑上做。");
  }
}

function chosen(): Pairing | undefined {
  try {
    return pairings().find((p) => p.room === new URLSearchParams(location.search).get("c"));
  } catch (error) {
    // The loader says what to do about a record it cannot read, or a browser that keeps none.
    if (error instanceof Unreadable || error instanceof DOMException) return undefined;
    throw error;
  }
}

const pairing = chosen();
const root = document.getElementById("root");
if (!pairing) {
  location.replace(LOADER);
} else if (root) {
  const link = new Link(pairing);
  mockWindows("main");
  mockIPC((command, args) => invoke(link, command, args), { shouldMockEvents: true });
  followTheme();
  link.connect();
  createRoot(root).render(
    <StrictMode>
      <Phone link={link} computer={pairing.computer} />
    </StrictMode>,
  );
}
