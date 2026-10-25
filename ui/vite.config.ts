import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Tauri loads the dev server from a fixed port (crates/app/tauri.conf.json).
export default defineConfig(({ command, mode }) => {
  const preview = command === "serve" && mode === "preview";
  if (preview && !process.env.AES_PREVIEW_API) {
    throw new Error("请从仓库根目录运行 pnpm preview，以便同时启动核心服务。");
  }
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
    ],
    ...(preview && {
      define: {
        "import.meta.env.AES_PREVIEW_API": JSON.stringify(process.env.AES_PREVIEW_API),
        "import.meta.env.AES_PREVIEW_DATA": JSON.stringify(process.env.AES_PREVIEW_DATA),
        "import.meta.env.AES_PREVIEW_VERSION": JSON.stringify(process.env.AES_PREVIEW_VERSION),
      },
    }),
    resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
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
