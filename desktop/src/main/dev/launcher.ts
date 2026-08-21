import { execFile, spawn, type ChildProcess } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";


export const DEV_SHUTDOWN_GRACE_MS = 4_000;

export type ForcedTermination = {
  command: string;
  args: string[];
};

export function buildForcedTermination(
  platform: NodeJS.Platform,
  pid: number,
): ForcedTermination | null {
  if (platform !== "win32") {
    return null;
  }
  return {
    command: "taskkill",
    args: ["/pid", String(pid), "/t", "/f"],
  };
}

function runDevDesktop(): ChildProcess {
  const require = createRequire(import.meta.url);
  const electronPath = require("electron") as string;
  const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../..");
  const child = spawn(electronPath, [".", "--dev"], {
    cwd: desktopRoot,
    stdio: "inherit",
    windowsHide: false,
  });
  let forceKillTimer: NodeJS.Timeout | null = null;
  let requestedSignal: NodeJS.Signals | null = null;

  const requestShutdown = (signal: NodeJS.Signals) => {
    if (child.exitCode !== null || child.signalCode !== null || requestedSignal !== null) {
      return;
    }
    requestedSignal = signal;
    if (process.platform !== "win32") {
      child.kill(signal);
    }
    forceKillTimer = setTimeout(() => {
      if (child.pid === undefined || child.exitCode !== null || child.signalCode !== null) {
        return;
      }
      const forcedTermination = buildForcedTermination(process.platform, child.pid);
      if (forcedTermination !== null) {
        execFile(
          forcedTermination.command,
          forcedTermination.args,
          { windowsHide: true },
          () => undefined,
        );
        return;
      }
      child.kill("SIGKILL");
    }, DEV_SHUTDOWN_GRACE_MS);
  };

  process.once("SIGINT", () => requestShutdown("SIGINT"));
  process.once("SIGTERM", () => requestShutdown("SIGTERM"));
  child.once("exit", (code, signal) => {
    if (forceKillTimer !== null) {
      clearTimeout(forceKillTimer);
    }
    process.exitCode = code ?? (signal === "SIGINT" ? 130 : signal === "SIGTERM" ? 143 : 1);
  });
  return child;
}


const entryPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : null;
if (entryPath === import.meta.url) {
  runDevDesktop();
}
