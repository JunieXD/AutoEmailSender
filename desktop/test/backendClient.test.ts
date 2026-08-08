import { describe, expect, it, vi } from "vitest";
import {
  buildBackendApiUrl,
  createDesktopBackendClient,
} from "../src/main/backend/client.js";

describe("desktop backend client", () => {
  it("uses the current connection and adds the bearer token", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response("ok"));
    const client = createDesktopBackendClient({
      getConnection: () => ({
        baseUrl: "http://127.0.0.1:8010/",
        accessToken: "  first-token ",
      }),
      dependencies: { fetch: fetchMock },
    });

    await client.request("/api/materials/42/download", {
      headers: { "X-Request-Id": "test" },
    });

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    const headers = new Headers(init?.headers);
    expect(url).toBe("http://127.0.0.1:8010/api/materials/42/download");
    expect(headers.get("X-Request-Id")).toBe("test");
    expect(headers.get("Authorization")).toBe("Bearer first-token");
  });

  it("reads the latest base URL and token for every request", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response("ok"));
    let connection = {
      baseUrl: "http://127.0.0.1:8010",
      accessToken: "first-token",
    };
    const client = createDesktopBackendClient({
      getConnection: () => connection,
      dependencies: { fetch: fetchMock },
    });

    await client.request("/api/health");
    connection = {
      baseUrl: "http://127.0.0.1:9010",
      accessToken: "second-token",
    };
    await client.request("/api/health");

    const requests = fetchMock.mock.calls.map(([url, init]) => ({
      url,
      authorization: new Headers(init?.headers).get("Authorization"),
    }));
    expect(requests).toEqual([
      { url: "http://127.0.0.1:8010/api/health", authorization: "Bearer first-token" },
      { url: "http://127.0.0.1:9010/api/health", authorization: "Bearer second-token" },
    ]);
  });

  it("rejects missing connections and tokens", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    let connection: { baseUrl: string; accessToken: string } | null = null;
    const client = createDesktopBackendClient({
      getConnection: () => connection,
      dependencies: { fetch: fetchMock },
    });

    await expect(client.request("/api/health")).rejects.toThrow("访问令牌");
    connection = { baseUrl: "http://127.0.0.1:8010", accessToken: "  " };
    await expect(client.request("/api/health")).rejects.toThrow("访问令牌");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("restricts requests to same-origin /api paths", async () => {
    const client = createDesktopBackendClient({
      getConnection: () => ({
        baseUrl: "http://127.0.0.1:8010",
        accessToken: "token",
      }),
      dependencies: { fetch: vi.fn<typeof fetch>() },
    });

    await expect(client.request("/internal/health")).rejects.toThrow("/api");
    await expect(client.request("https://example.com/api/health")).rejects.toThrow("/api");
    expect(buildBackendApiUrl("http://127.0.0.1:8010", "/api/health")).toBe(
      "http://127.0.0.1:8010/api/health",
    );
  });

  it("overwrites caller authorization with the current token", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response("ok"));
    const client = createDesktopBackendClient({
      getConnection: () => ({
        baseUrl: "http://127.0.0.1:8010",
        accessToken: "current-token",
      }),
      dependencies: { fetch: fetchMock },
    });

    await client.request("/api/health", {
      headers: { Authorization: "Bearer attacker-token" },
    });

    const init = fetchMock.mock.calls[0]?.[1];
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer current-token");
  });
});
