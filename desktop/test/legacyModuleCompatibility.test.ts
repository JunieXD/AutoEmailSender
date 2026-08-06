import { readFileSync } from "node:fs";
import path from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";


const legacyOwners = [
  {
    legacyPath: "agentRuntime.ts",
    loadLegacy: () => import("../src/agentRuntime.js"),
    loadOwner: () => import("../src/main/agent-support/runtime.js"),
  },
  {
    legacyPath: "agentSupportService.ts",
    loadLegacy: () => import("../src/agentSupportService.js"),
    loadOwner: () => import("../src/main/agent-support/service.js"),
  },
  {
    legacyPath: "backend.ts",
    loadLegacy: () => import("../src/backend.js"),
    loadOwner: () => import("../src/main/backend/service.js"),
  },
  {
    legacyPath: "externalUrlService.ts",
    loadLegacy: () => import("../src/externalUrlService.js"),
    loadOwner: () => import("../src/main/shell/external-url.js"),
  },
  {
    legacyPath: "fileSelection.ts",
    loadLegacy: () => import("../src/fileSelection.js"),
    loadOwner: () => import("../src/main/files/import-export.js"),
  },
  {
    legacyPath: "macSparkle.ts",
    loadLegacy: () => import("../src/macSparkle.js"),
    loadOwner: () => import("../src/main/updates/sparkle.js"),
  },
  {
    legacyPath: "materialOpenService.ts",
    loadLegacy: () => import("../src/materialOpenService.js"),
    loadOwner: () => import("../src/main/files/material-open.js"),
  },
  {
    legacyPath: "prepareDevCli.ts",
    loadLegacy: () => import("../src/prepareDevCli.js"),
    loadOwner: () => import("../src/main/agent-support/prepare-dev-cli.js"),
  },
  {
    legacyPath: "startup.ts",
    loadLegacy: () => import("../src/startup.js"),
    loadOwner: () => import("../src/main/shell/startup-at-login.js"),
  },
  {
    legacyPath: "trayController.ts",
    loadLegacy: () => import("../src/trayController.js"),
    loadOwner: () => import("../src/main/shell/tray.js"),
  },
  {
    legacyPath: "updates.ts",
    loadLegacy: () => import("../src/updates.js"),
    loadOwner: () => import("../src/main/updates/service.js"),
  },
  {
    legacyPath: "windowIcon.ts",
    loadLegacy: () => import("../src/windowIcon.js"),
    loadOwner: () => import("../src/main/shell/window-icon.js"),
  },
  {
    legacyPath: "windowLifecycle.ts",
    loadLegacy: () => import("../src/windowLifecycle.js"),
    loadOwner: () => import("../src/main/shell/window-lifecycle.js"),
  },
] as const;

describe("desktop legacy module compatibility", () => {
  it("keeps root compatibility modules as pure re-exports", () => {
    const legacyPaths = [...legacyOwners.map(({ legacyPath }) => legacyPath), "types.ts"];
    for (const legacyPath of legacyPaths) {
      const absolutePath = path.resolve("src", legacyPath);
      const sourceFile = ts.createSourceFile(
        absolutePath,
        readFileSync(absolutePath, "utf8"),
        ts.ScriptTarget.Latest,
        true,
        ts.ScriptKind.TS,
      );

      expect(sourceFile.statements.length, legacyPath).toBeGreaterThan(0);
      for (const statement of sourceFile.statements) {
        expect(ts.isExportDeclaration(statement), legacyPath).toBe(true);
        expect(
          ts.isExportDeclaration(statement) && statement.moduleSpecifier !== undefined,
          legacyPath,
        ).toBe(true);
      }
    }
  });

  it("re-exports the owner module values by identity", async () => {
    for (const { legacyPath, loadLegacy, loadOwner } of legacyOwners) {
      const legacy = await loadLegacy();
      const owner = await loadOwner();
      const legacyExports = legacy as Record<string, unknown>;
      const ownerExports = owner as Record<string, unknown>;

      expect(Object.keys(legacyExports).sort(), legacyPath).toEqual(
        Object.keys(ownerExports).sort(),
      );
      for (const name of Object.keys(ownerExports)) {
        expect(legacyExports[name], `${legacyPath}:${name}`).toBe(ownerExports[name]);
      }
    }
  });
});
