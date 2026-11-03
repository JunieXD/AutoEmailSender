/// <reference types="vitest/config" />
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

/**
 * Builds the page at `html` alone, as the `index.html` the server gives for its directory: the
 * phone's pages (docs/development.md#手机远程), which the server keeps apart from the desktop app.
 */
function entry(html: string): Plugin {
  return {
    name: "aes-entry",
    // After Vite has written the HTML.
    enforce: "post",
    config: () => ({ build: { rolldownOptions: { input: html } } }),
    generateBundle(_, bundle) {
      const page = bundle[html];
      if (page?.type !== "asset") throw new Error(`${html} was not built`);
      delete bundle[html];
      this.emitFile({ type: "asset", fileName: "index.html", source: page.source });
    },
  };
}

// Tauri loads the dev server from a fixed port (crates/app/tauri.conf.json).
export default defineConfig(({ command, mode }) => {
  const preview = command === "serve" && mode === "preview";
  if (preview && !process.env.AES_PREVIEW_API) {
    throw new Error("请从仓库根目录运行 pnpm preview，以便同时启动核心服务。");
  }
  const phone = mode === "phone";
  if (phone && !process.env.AES_PHONE_VERSION) throw new Error("请用 cargo xtask phone 构建手机页面。");
  return {
    plugins: [
      react(),
      tailwindcss(),
      preview && {
        name: "aes-browser-preview",
        transformIndexHtml: () => [
          { tag: "script", attrs: { type: "module", src: "/src/preview.ts" }, injectTo: "head-prepend" },
        ],
      },
      phone && entry("phone.html"),
      mode === "loader" && entry("phone-loader/index.html"),
    ],
    ...(preview && {
      define: {
        "import.meta.env.AES_PREVIEW_API": JSON.stringify(process.env.AES_PREVIEW_API),
        "import.meta.env.AES_PREVIEW_DATA": JSON.stringify(process.env.AES_PREVIEW_DATA),
        "import.meta.env.AES_PREVIEW_VERSION": JSON.stringify(process.env.AES_PREVIEW_VERSION),
      },
    }),
    ...(phone && {
      base: `/m/${process.env.AES_PHONE_VERSION}/`,
      define: { "import.meta.env.AES_PHONE_VERSION": JSON.stringify(process.env.AES_PHONE_VERSION) },
    }),
    ...(mode === "loader" && { base: "/m/" }),
    resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
    // Windows CI runners can take seconds to load the editor or the Chinese word segmenter in a test
    // that otherwise runs in milliseconds; the limit is only there to stop a test that hangs.
    test: { testTimeout: 30_000 },
    clearScreen: false,
    server: {
      port: 1420,
      strictPort: true,
      // Only the app's own pages load from the dev server; no other site may read it, and nobody may
      // fetch the development data (database, mailbox passwords, API keys) through it.
      cors: false,
      fs: { deny: [".env", ".env.*", "*.{crt,pem}", "**/.git/**", "**/.dev-data/**"] },
    },
  };
});
