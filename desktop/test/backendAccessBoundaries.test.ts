import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const sourceRoot = path.resolve(process.cwd(), "src");
const protectedRoutePattern = /\/api(?:[/?#]|$)/;
const allowedRouteFiles = new Set([
  "main/backend/client.ts",
  "main/shell/backend-navigation-guard.ts",
]);

const listProductionSourceFiles = (directory: string): string[] => readdirSync(directory, {
  withFileTypes: true,
}).flatMap((entry) => {
  const absolute = path.join(directory, entry.name);
  if (entry.isDirectory()) {
    return listProductionSourceFiles(absolute);
  }
  if (!entry.name.match(/\.tsx?$/) || entry.name.endsWith(".d.ts") || entry.name.includes(".test.")) {
    return [];
  }
  return [absolute];
});

const relativeFile = (file: string): string => path.relative(sourceRoot, file).split(path.sep).join("/");

const isBackendClientRequestArgument = (node: ts.Node): boolean => {
  let current = node.parent;
  while (current) {
    if (ts.isCallExpression(current)) {
      const expression = current.expression;
      if (ts.isPropertyAccessExpression(expression) && expression.name.text === "request") {
        return current.arguments.some((argument) => argument === node || argument.getStart() <= node.getStart() && argument.getEnd() >= node.getEnd());
      }
    }
    if (ts.isStatement(current)) {
      return false;
    }
    current = current.parent;
  }
  return false;
};

export function findBackendAccessViolations(file: string, sourceText: string): string[] {
  const sourceFile = ts.createSourceFile(
    file,
    sourceText,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const violations = new Set<string>();
  const allowDirectFetch = file === "main/backend/client.ts";
  const allowRawRoutes = allowedRouteFiles.has(file);
  const report = (node: ts.Node, message: string): void => {
    const line = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;
    violations.add(`${file}:${line} ${message}`);
  };

  const visit = (node: ts.Node): void => {
    if (ts.isCallExpression(node)) {
      const expression = node.expression;
      const property = ts.isPropertyAccessExpression(expression) ? expression.name.text : null;
      if (!allowDirectFetch && ((ts.isIdentifier(expression) && expression.text === "fetch") || property === "fetch")) {
        report(node, "must not call fetch directly; use main/backend/client.ts");
      }
    }

    if (
      !allowRawRoutes
      && (ts.isStringLiteralLike(node) || ts.isTemplateHead(node) || ts.isTemplateMiddle(node))
      && protectedRoutePattern.test(node.text)
      && !isBackendClientRequestArgument(node)
    ) {
      report(node, "must not declare protected /api routes outside the backend client");
    }

    ts.forEachChild(node, visit);
  };

  visit(sourceFile);
  return [...violations].sort();
}

describe("desktop backend access boundaries", () => {
  it("keeps production backend requests behind the desktop backend client", () => {
    const violations = listProductionSourceFiles(sourceRoot).flatMap((file) =>
      findBackendAccessViolations(relativeFile(file), readFileSync(file, "utf8")),
    );

    expect(violations).toEqual([]);
  });

  it("detects direct fetch and raw protected routes", () => {
    const source = `
      fetch('/api/export');
      window.fetch('/api/export');
      const endpoint = '/api/export';
    `;

    expect(findBackendAccessViolations("main/files/unsafe.ts", source)).toEqual(
      expect.arrayContaining([
        expect.stringContaining("must not call fetch directly"),
        expect.stringContaining("must not declare protected /api routes"),
      ]),
    );
  });

  it("allows protected routes passed directly to backendClient.request", () => {
    const source = `
      backendClient.request('/api/export');
    `;

    expect(findBackendAccessViolations("main/files/safe.ts", source)).toEqual([]);
  });
});
