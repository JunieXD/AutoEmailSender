// The page at /m/ (docs/development.md#手机远程): keeps the pairing a scanned code carries, picks
// the computer, and opens the page of the version its app runs.
import { forget, forgetAll, type Pairing, pairings, save, scanned, Unreadable } from "../src/phone/pairings";

/** How often to look again while the computer is not online. */
const AGAIN = 10_000;

const main = document.querySelector("main");
let timer: ReturnType<typeof setTimeout> | undefined;

function show(...nodes: Node[]) {
  clearTimeout(timer);
  main?.replaceChildren(...nodes);
}

function text(content: string) {
  const p = document.createElement("p");
  p.textContent = content;
  return p;
}

function button(label: string, onClick: () => void) {
  const b = document.createElement("button");
  b.textContent = label;
  b.addEventListener("click", onClick);
  return b;
}

/** Whether the computer is online, with the version its app runs; null when the server is out of reach. */
async function status(room: string): Promise<{ online: true; version: string } | { online: false } | null> {
  try {
    const response = await fetch(`/v1/relay/${room}`, { cache: "no-store" });
    if (!response.ok) return null;
    const body: unknown = await response.json();
    if (typeof body !== "object" || body === null || !("online" in body) || !("version" in body)) return null;
    const { online, version } = body;
    if (online === false) return { online };
    // The version names a directory of the address; the server takes only these.
    if (online !== true || typeof version !== "string" || !validVersion(version)) return null;
    return { online, version };
  } catch {
    return null;
  }
}

function validVersion(version: string) {
  return version.length <= 40 && /^\d[0-9A-Za-z.-]*$/.test(version) && !version.includes("..");
}

async function open(pairing: Pairing) {
  show(text(pairing.computer === null ? "正在连接电脑…" : `正在连接 ${pairing.computer}…`));
  const found = await status(pairing.room);
  if (found?.online) {
    location.replace(`/m/${found.version}/?c=${pairing.room}`);
    return;
  }
  const others = pairings().length > 1;
  show(
    text(found ? "电脑上的应用没有连上：请确认它开着、已登录、能联网。" : "连不上服务器，请检查手机的网络。"),
    button("重试", () => void open(pairing)),
    ...(others ? [button("换一台电脑", choose)] : []),
  );
  timer = setTimeout(() => void open(pairing), AGAIN);
}

function choose() {
  const list = pairings();
  if (list.length === 0) {
    show(text("在电脑上的应用里打开 设置 › 手机和 Agent，按“添加手机”，用手机扫描二维码。"));
    return;
  }
  const items = list.map((pairing) => {
    const li = document.createElement("li");
    li.append(
      button(pairing.computer ?? "还没连上过的电脑", () => void open(pairing)),
      // Only this phone's record; the computer keeps its own until revoked there.
      button("删掉", () => {
        forget(pairing.room);
        choose();
      }),
    );
    return li;
  });
  const ul = document.createElement("ul");
  ul.append(...items);
  show(text("选择要连接的电脑"), ul);
}

function start(code: Pairing | null) {
  try {
    if (code) save(code);
    const list = pairings();
    const room = code?.room ?? new URLSearchParams(location.search).get("c");
    const chosen = list.find((p) => p.room === room) ?? (list.length === 1 ? list[0] : undefined);
    if (chosen) void open(chosen);
    else choose();
  } catch (error) {
    if (error instanceof Unreadable) {
      show(
        text("这部手机上记下的电脑读不出来了，清除后才能接着用。清除以后，没连上的电脑要重新扫码。"),
        button("清除", () => {
          forgetAll();
          start(code);
        }),
      );
    } else if (error instanceof DOMException) {
      // The browser keeps no data for the page, as in private browsing on some phones.
      show(
        text(
          "这个浏览器不让网页保存数据，记不住配对：请关掉无痕浏览，或者允许这个网站保存数据，再在电脑上重新生成二维码。",
        ),
      );
    } else {
      throw error;
    }
  }
}

const code = scanned(location.hash);
// The key stays out of the address bar, the history and bookmarks, even when it cannot be kept.
if (code) history.replaceState(null, "", location.pathname);
start(code);
