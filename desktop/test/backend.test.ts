import { EventEmitter } from "node:events";
import type { ChildProcessWithoutNullStreams } from "node:child_process";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { describe, expect, it } from "vitest";
import {
  buildBackendEnv,
  getBackendExecutablePath,
  getFrontendIndexPath,
  notifyBackendExit,
  normalizePort,
  stopBackend,
  waitForHealth,
  waitForStartupStatus,
} from "../src/backend.js";

type StartupStatusFixture = {
  state: "starting" | "ready" | "error";
  phase: string;
  message: string;
  elapsed_seconds: number;
  error: string | null;
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
        resourcesPath: "C:\\App\\resources",
        repoRoot: "C:\\Repo",
      }),
    ).toBe("C:\\App\\resources\\backend\\backend.exe");
  });

  it("resolves dev backend entry path", () => {
    expect(
      getBackendExecutablePath({
        isPackaged: false,
        resourcesPath: "C:\\App\\resources",
        repoRoot: "C:\\Repo",
      }),
    ).toBe("C:\\Repo\\backend\\desktop_entry.py");
  });

  it("resolves packaged frontend index path", () => {
    expect(
      getFrontendIndexPath({
        isPackaged: true,
        resourcesPath: "C:\\App\\resources",
        repoRoot: "C:\\Repo",
      }),
    ).toBe("C:\\App\\resources\\frontend\\index.html");
  });

  it("builds backend environment with desktop data dir", () => {
    const env = buildBackendEnv({
      baseEnv: { PATH: "C:\\Windows" },
      isPackaged: true,
      repoRoot: "C:\\Repo",
      resourcesPath: "C:\\App\\resources",
      userDataPath: "C:\\Users\\Alice\\AppData\\Roaming\\Auto Email Sender",
    });

    expect(env.PATH).toBe("C:\\Windows");
    expect(env.AUTO_EMAIL_SENDER_DATA_DIR).toBe(
      "C:\\Users\\Alice\\AppData\\Roaming\\Auto Email Sender",
    );
    expect(env.ENABLE_BACKGROUND_WORKERS).toBe("true");
    expect(env.PLAYWRIGHT_BROWSERS_PATH).toBe("C:\\App\\resources\\ms-playwright");
  });

  it("uses repo browser cache for dev backend environment", () => {
    const env = buildBackendEnv({
      baseEnv: {},
      isPackaged: false,
      resourcesPath: "C:\\App\\resources",
      repoRoot: "C:\\Repo",
      userDataPath: "C:\\Users\\Alice\\AppData\\Roaming\\Auto Email Sender",
    });

    expect(env.PLAYWRIGHT_BROWSERS_PATH).toBe("C:\\Repo\\backend\\ms-playwright");
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

  it("terminates the backend process tree during stop", async () => {
    const child = Object.assign(new EventEmitter(), {
      pid: 1234,
      exitCode: null as number | null,
      kill: () => {
        throw new Error("direct child kill should not be used when a pid is available");
      },
    }) as unknown as ChildProcessWithoutNullStreams;
    const terminatedPids: number[] = [];

    await stopBackend(
      child,
      { intentionalStop: false },
      async (pid) => {
        terminatedPids.push(pid);
        Object.assign(child, { exitCode: 0 });
        child.emit("exit", 0, null);
      },
    );

    expect(terminatedPids).toEqual([1234]);
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
