import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { loadEnv } from "vite";
import { fileURLToPath, URL } from "node:url";
import { getApiProxyTarget } from "./vite.proxy";

const nodeTestFiles = [
  "src/features/crawl-review/client/reviewCandidates.test.ts",
  "src/features/create-task/client/scheduleDates.test.ts",
  "src/features/home-dashboard/client/*.test.ts",
  "src/features/match-analysis/client/tokenUsage.test.ts",
  "src/features/professor-management/client/*.test.ts",
  "src/features/token-usage/client/tokenUsage.test.ts",
  "src/lib/dateTime.test.ts",
  "src/lib/charting.test.ts",
  "src/lib/pagination.test.ts",
  "src/lib/professorTitle.test.ts",
  "src/pages/ProfilePage.test.ts",
  "test/BatchTasksApi.test.ts",
  "test/CrawlJobsApi.test.ts",
  "test/MatchAnalysisJobsApi.test.ts",
  "test/desktopPackaging.test.ts",
  "test/favicon.test.ts",
  "test/getOnboardingState.test.ts",
  "test/getWorkspaceNextStep.test.ts",
  "test/mentorFilter.test.ts",
  "test/notifications.test.ts",
  "test/professorDashboardStatus.test.ts",
  "test/taskCopy.test.ts",
  "test/templatePlaceholders.test.ts",
  "test/validateTaskForm.test.ts",
  "test/viteConfig.test.ts",
  "vite.config.test.ts",
];

const jsdomTestFiles = [
  "src/**/*.test.tsx",
  "test/**/*.test.tsx",
  "src/lib/api/client.test.ts",
  "src/lib/desktopApi.test.ts",
  "test/apiClient.test.ts",
  "test/diagnostics.test.ts",
  "test/htmlPreview.test.ts",
  "test/richEmail.test.ts",
];

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");

  return {
    base: "./",
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
    },
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/api": {
          target: getApiProxyTarget(env),
          changeOrigin: true,
        },
      },
    },
    test: {
      projects: [
        {
          extends: true,
          test: {
            name: "node",
            environment: "node",
            include: nodeTestFiles,
          },
        },
        {
          extends: true,
          test: {
            name: "jsdom",
            environment: "jsdom",
            setupFiles: "./test/setup.ts",
            include: jsdomTestFiles,
          },
        },
      ],
    },
  };
});
