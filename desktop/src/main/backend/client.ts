import type { DesktopBackendConnection } from "../../../../contracts/desktop-ipc.js";

export type DesktopBackendClient = {
  request: (path: string, options?: RequestInit) => Promise<Response>;
};

export type DesktopBackendClientOptions = {
  getConnection: () => DesktopBackendConnection | null | undefined;
  dependencies?: {
    fetch?: typeof fetch;
  };
};

export function createDesktopBackendClient(
  options: DesktopBackendClientOptions,
): DesktopBackendClient {
  const requestFetch = options.dependencies?.fetch ?? fetch;

  return {
    async request(path: string, requestOptions?: RequestInit): Promise<Response> {
      const connection = options.getConnection();
      if (!connection?.baseUrl?.trim() || !connection.accessToken?.trim()) {
        throw new Error("本地系统服务尚未准备好访问令牌");
      }

      const url = buildBackendApiUrl(connection.baseUrl, path);
      const headers = new Headers(requestOptions?.headers);
      headers.set("Authorization", `Bearer ${connection.accessToken.trim()}`);

      return requestFetch(url, {
        ...requestOptions,
        headers,
      });
    },
  };
}

export function buildBackendApiUrl(baseUrl: string, path: string): string {
  if (path !== "/api" && !path.startsWith("/api/")) {
    throw new Error("桌面后端客户端只允许访问 /api 接口");
  }

  const base = new URL(baseUrl);
  const target = new URL(path, base);
  if (target.origin !== base.origin) {
    throw new Error("桌面后端客户端拒绝跨源接口地址");
  }
  return target.toString();
}
