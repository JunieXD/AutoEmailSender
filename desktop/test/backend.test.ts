import { EventEmitter } from "node:events";
import path from "node:path";
import type { ChildProcessWithoutNullStreams } from "node:child_process";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { describe, expect, it } from "vitest";
import {
  DEFAULT_BACKEND_READY_POLL_INTERVAL_MS,
  buildBackendEnv,
  fetchBackendRuntimeInfo,
  getBackendExecutablePath,
  getFrontendIndexPath,
  generateAccessToken,
  notifyBackendExit,
  normalizePort,
  shouldDetachBackend,
  stopBackend,
  waitForHealth,
  waitForStartupStatus,
} from "../src/main/backend/service.js";

type StartupErrorDetailFixture = {
  code: "DATABASE_REQUIRES_NEWER_APP";
  message: string;
  current_app_version: string;
  minimum_supported_app_version: string;
  backup_directory: string;
  suggested_actions: string[];
};

type StartupStatusFixture = {
  state: "starting" | "ready" | "error";
  phase: string;
  message: string;
  elapsed_seconds: number;
  error: string | null;
  error_detail?: StartupErrorDetailFixture | null;
};

async function withStartupServer(
  statuses: StartupStatusFixture[],
  test: (baseUrl: string) => Promise<void>,
): Promise<void> {
  let statusIndex = 0;
  const server = createServer((request, response) => {
    if (request.url === "/health") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ status: "ok" }));
      return;
    }

    if (request.url === "/startup-status") {
      const status = statuses[Math.min(statusIndex, statuses.length - 1)];
      statusIndex += 1;
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify(status));
      return;
    }

    response.writeHead(404);
    response.end();
  });

  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as AddressInfo;
  try {
    await test(`http://127.0.0.1:${address.port}`);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((error) => {
        if (error) {
          reject(error);
          return;
        }
        resolve();
      });
    });
  }
}

function createRunningChildProcess(): ChildProcessWithoutNullStreams {
  return Object.assign(new EventEmitter(), {
    exitCode: null,
    stderr: new EventEmitter(),
  }) as unknown as ChildProcessWithoutNullStreams;
}

describe("desktop backend helpers", () => {
  it("resolves packaged backend executable path", () => {
    expect(
      getBackendExecutablePath({
        isPackaged: true,
        platform: "win32",
        resourcesPath: "C:\\App\\resources",
        repoRoot: "C:\\Repo",
      }),
    ).toBe(path.join("C:\\App\\resources", "backend", "backend.exe"));
  });

  it("resolves packaged backend executable path on macOS", () => {
    expect(
      getBackendExecutablePath({
        isPackaged: true,
        platform: "darwin",
        resourcesPath: "/Applications/Auto Email Sender.app/Contents/Resources",
        repoRoot: "/repo",
      }),
    ).toBe(path.join("/Applications/Auto Email Sender.app/Contents/Resources", "backend", "backend"));
  });

  it("resolves dev backend entry path", () => {
    expect(
      getBackendExecutablePath({
        isPackaged: false,
        resourcesPath: "C:\\App\\resources",
        repoRoot: "C:\\Repo",
      }),
    ).toBe(path.join("C:\\Repo", "backend", "desktop_entry.py"));
  });

  it("resolves packaged frontend index path", () => {
    expect(
      getFrontendIndexPath({
        isPackaged: true,
        resourcesPath: "C:\\App\\resources",
        repoRoot: "C:\\Repo",
      }),
    ).toBe(path.join("C:\\App\\resources", "frontend", "index.html"));
  });

  it("builds backend environment with desktop data dir", () => {
    const baseEnv = { PATH: "C:\\Windows" };
    const env = buildBackendEnv({
      baseEnv,
      isPackaged: true,
      repoRoot: "C:\\Repo",
      resourcesPath: "C:\\App\\resources",
      userDataPath: "C:\\Users\\Alice\\AppData\\Roaming\\auto-email-sender-desktop",
      appVersion: "2.4.5",
      electronExecutablePath: "C:\\Program Files\\Auto Email Sender\\Auto Email Sender.exe",
      runtimeId: "runtime-test",
      uiAccessToken: "ui-token",
      agentAccessToken: "agent-token",
    });

    expect(env.PATH).toBe("C:\\Windows");
    expect(env.AUTO_EMAIL_SENDER_DATA_DIR).toBe(
      "C:\\Users\\Alice\\AppData\\Roaming\\auto-email-sender-desktop",
    );
    expect(env.ENABLE_BACKGROUND_WORKERS).toBe("true");
    expect(env.AUTO_EMAIL_SENDER_APP_VERSION).toBe("2.4.5");
    expect(env.AUTO_EMAIL_SENDER_DESKTOP_PID).toBe(String(process.pid));
    expect(env.AUTO_EMAIL_SENDER_RUNTIME_ID).toBe("runtime-test");
    expect(env.PLAYWRIGHT_BROWSERS_PATH).toBe(path.join("C:\\App\\resources", "ms-playwright"));
    expect(env.PLAYWRIGHT_NODEJS_PATH).toBe(
      "C:\\Program Files\\Auto Email Sender\\Auto Email Sender.exe",
    );
    expect(env.ELECTRON_RUN_AS_NODE).toBe("1");
    expect(env.AUTO_EMAIL_SENDER_UI_TOKEN).toBe("ui-token");
    expect(env.AUTO_EMAIL_SENDER_AGENT_TOKEN).toBe("agent-token");
    expect(baseEnv).toEqual({ PATH: "C:\\Windows" });
  });

  it("generates high-entropy URL-safe access tokens", () => {
    const first = generateAccessToken();
    const second = generateAccessToken();

    expect(first).not.toBe(second);
    expect(first).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(second).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });

  it("fetches authenticated runtime identity from the backend", async () => {
    const server = createServer((request, response) => {
      expect(request.url).toBe("/api/agent/v1/runtime");
      expect(request.headers.authorization).toBe("Bearer agent-token");
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({
        runtime_id: "runtime-test",
        protocol_version: "3",
        app_version: "2.5.4",
        backend_pid: 4321,
        desktop_pid: process.pid,
        state: "ready",
      }));
    });

    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address() as AddressInfo;
    try {
      await expect(
        fetchBackendRuntimeInfo(`http://127.0.0.1:${address.port}`, "agent-token"),
      ).resolves.toEqual({
        runtime_id: "runtime-test",
        protocol_version: "3",
        app_version: "2.5.4",
        backend_pid: 4321,
        desktop_pid: process.pid,
        state: "ready",
      });
    } finally {
      await new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      });
    }
  });

  it("uses repo browser cache for dev backend environment", () => {
    const env = buildBackendEnv({
      baseEnv: {},
      isPackaged: false,
      resourcesPath: "C:\\App\\resources",
      repoRoot: "C:\\Repo",
      userDataPath: "C:\\Users\\Alice\\AppData\\Roaming\\auto-email-sender-desktop",
      appVersion: "2.4.5",
      electronExecutablePath: "C:\\Repo\\desktop\\node_modules\\electron\\dist\\electron.exe",
      runtimeId: "runtime-dev",
    });

    expect(env.PLAYWRIGHT_BROWSERS_PATH).toBe(path.join("C:\\Repo", "backend", "ms-playwright"));
    expect(env.PLAYWRIGHT_NODEJS_PATH).toBeUndefined();
    expect(env.ELECTRON_RUN_AS_NODE).toBeUndefined();
  });

  it("allows backend controllers to expose readiness separately from process launch", async () => {
    let markReady: (() => void) | undefined;
    const controller = {
      baseUrl: "http://127.0.0.1:48123",
      ready: new Promise<void>((resolve) => {
        markReady = resolve;
      }),
      stop: async () => undefined,
    };
    let ready = false;
    void controller.ready.then(() => {
      ready = true;
    });

    await Promise.resolve();
    expect(controller.baseUrl).toBe("http://127.0.0.1:48123");
    expect(ready).toBe(false);

    markReady?.();
    await controller.ready;
    expect(ready).toBe(true);
  });

  it("normalizes valid ports", () => {
    expect(normalizePort("48123")).toBe(48123);
  });

  it("notifies when backend exits unexpectedly", () => {
    const exits: Array<{ code: number | null; signal: NodeJS.Signals | null }> = [];

    notifyBackendExit(
      {
        intentionalStop: false,
        onUnexpectedExit: (exit) => exits.push(exit),
      },
      1,
      null,
    );

    expect(exits).toEqual([{ code: 1, signal: null }]);
  });

  it("does not notify when backend exits during intentional stop", () => {
    const exits: Array<{ code: number | null; signal: NodeJS.Signals | null }> = [];

    notifyBackendExit(
      {
        intentionalStop: true,
        onUnexpectedExit: (exit) => exits.push(exit),
      },
      0,
      null,
    );

    expect(exits).toEqual([]);
  });

  it("keeps the dev backend attached to the terminal on Unix", () => {
    expect(shouldDetachBackend({ isPackaged: false, platform: "linux" })).toBe(false);
    expect(shouldDetachBackend({ isPackaged: false, platform: "darwin" })).toBe(false);
  });

  it("targets the direct backend process and port-specific descendants during Unix stop", async () => {
    const child = Object.assign(new EventEmitter(), {
      pid: 1234,
      exitCode: null as number | null,
      kill: () => {
        throw new Error("child.kill should not be used when a pid is available");
      },
    }) as unknown as ChildProcessWithoutNullStreams;
    const terminations: Array<{ pid: number; port?: number }> = [];

    await stopBackend(
      child,
      { intentionalStop: false },
      async (pid, port) => {
        terminations.push({ pid, port });
        Object.assign(child, { exitCode: 0 });
        child.emit("exit", 0, null);
      },
      48120,
    );

    expect(terminations).toEqual([{ pid: 1234, port: 48120 }]);
  });

  it("uses a 60 second default health check timeout", async () => {
    const source = await import("node:fs/promises").then((fs) =>
      fs.readFile(path.resolve("src", "main", "backend", "service.ts"), "utf8"),
    );

    expect(source).toContain("const timeoutMs = options.timeoutMs ?? 60_000;");
  });

  it("uses a shared fast polling interval for backend readiness", () => {
    expect(DEFAULT_BACKEND_READY_POLL_INTERVAL_MS).toBe(200);
  });

  it("keeps waiting after slow health startup threshold", async () => {
    const observed: string[] = [];
    const child = createRunningChildProcess();
    const server = createServer((request, response) => {
      if (request.url === "/health") {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ status: "ok" }));
        return;
      }
      response.writeHead(404);
      response.end();
    });

    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address() as AddressInfo;

    try {
      await expect(
        waitForHealth(`http://127.0.0.1:${address.port}`, child, () => "", {
          pollIntervalMs: 1,
          timeoutMs: 1_000,
          slowStartupMs: 0,
          onStatus: (status) => {
            if (status.state === "starting") {
              observed.push(status.message);
            }
          },
        }),
      ).resolves.toBeUndefined();
    } finally {
      await new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      });
    }

    expect(observed).toContain("首次启动可能较慢，正在继续等待本地服务");
  });

  it("polls startup status until the backend is ready", async () => {
    const observed: string[] = [];

    await withStartupServer(
      [
        {
          state: "starting",
          phase: "migrating_database",
          message: "正在检查和升级本地数据",
          elapsed_seconds: 3,
          error: null,
        },
        {
          state: "ready",
          phase: "ready",
          message: "系统已准备就绪",
          elapsed_seconds: 4,
          error: null,
        },
      ],
      async (baseUrl) => {
        await expect(
          waitForStartupStatus(baseUrl, {
            onStatus: (status) => observed.push(status.state),
            pollIntervalMs: 1,
            hardTimeoutMs: 1_000,
          }),
        ).resolves.toBeUndefined();
      },
    );

    expect(observed).toEqual(["starting", "ready"]);
  });

  it("keeps polling when a startup status request fails temporarily", async () => {
    const observed: string[] = [];
    let statusRequests = 0;
    const server = createServer((request, response) => {
      if (request.url === "/startup-status") {
        statusRequests += 1;
        if (statusRequests === 1) {
          response.writeHead(503, { "Content-Type": "application/json" });
          response.end(JSON.stringify({ error: "temporarily unavailable" }));
          return;
        }

        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(
          JSON.stringify({
            state: "ready",
            phase: "ready",
            message: "系统已准备就绪",
            elapsed_seconds: 4,
            error: null,
          }),
        );
        return;
      }

      response.writeHead(404);
      response.end();
    });

    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address() as AddressInfo;

    try {
      await expect(
        waitForStartupStatus(`http://127.0.0.1:${address.port}`, {
          onStatus: (status) => observed.push(status.state),
          pollIntervalMs: 1,
          hardTimeoutMs: 1_000,
        }),
      ).resolves.toBeUndefined();
    } finally {
      await new Promise<void>((resolve, reject) => {
        server.close((error) => {
          if (error) {
            reject(error);
            return;
          }
          resolve();
        });
      });
    }

    expect(statusRequests).toBe(2);
    expect(observed).toEqual(["starting", "ready"]);
  });

  it("fails startup polling immediately when the backend process exits", async () => {
    const child = createRunningChildProcess();
    const server = createServer((request, response) => {
      if (request.url === "/startup-status") {
        response.writeHead(503, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "temporarily unavailable" }));
        return;
      }

      response.writeHead(404);
      response.end();
    });

    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address() as AddressInfo;

    try {
      setTimeout(() => {
        Object.assign(child, { exitCode: 1 });
        child.emit("exit", 1, null);
      }, 10);

      await expect(
        waitForStartupStatus(`http://127.0.0.1:${address.port}`, {
          child,
          getStderr: () => "startup failed",
          onStatus: () => undefined,
          pollIntervalMs: 5,
          hardTimeoutMs: 500,
        }),
      ).rejects.toThrow("后端进程已退出：startup failed");
    } finally {
      await new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      });
    }
  });


  it("maps database version startup errors to structured backend status", async () => {
    const observed: unknown[] = [];

    await withStartupServer(
      [
        {
          state: "error",
          phase: "error",
          message: "系统准备失败",
          elapsed_seconds: 5,
          error: "当前数据由较新版本创建，当前版本无法直接打开。",
          error_detail: {
            code: "DATABASE_REQUIRES_NEWER_APP",
            message: "当前数据由较新版本创建，当前版本无法直接打开。",
            current_app_version: "2.3.0",
            minimum_supported_app_version: "2.4.0",
            backup_directory: "C:\\Users\\Alice\\AppData\\Roaming\\AutoEmailSender\\backups\\schema",
            suggested_actions: ["安装 2.4.0 或更高版本继续使用", "如需回退，请从升级前备份恢复数据库"],
          },
        },
      ],
      async (baseUrl) => {
        await expect(
          waitForStartupStatus(baseUrl, {
            onStatus: (status) => observed.push(status),
            pollIntervalMs: 1,
            hardTimeoutMs: 1_000,
          }),
        ).rejects.toThrow("系统准备失败");
      },
    );

    expect(observed).toEqual([
      expect.objectContaining({
        state: "error",
        databaseError: expect.objectContaining({
          code: "DATABASE_REQUIRES_NEWER_APP",
          minimumSupportedAppVersion: "2.4.0",
        }),
      }),
    ]);
  });
  it("fails startup polling when startup status reports error", async () => {
    await withStartupServer(
      [
        {
          state: "error",
          phase: "error",
          message: "系统准备失败",
          elapsed_seconds: 5,
          error: "database is locked",
        },
      ],
      async (baseUrl) => {
        await expect(
          waitForStartupStatus(baseUrl, {
            onStatus: () => undefined,
            pollIntervalMs: 1,
            hardTimeoutMs: 1_000,
          }),
        ).rejects.toThrow("系统准备失败");
      },
    );
  });
});
