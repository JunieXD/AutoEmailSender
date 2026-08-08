import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const sourceRoot = path.resolve(process.cwd(), "src");
const apiRoutePattern = /\/api(?:[/?#]|$)/;
const lowLevelClientExports = new Set([
  "apiFetchBlob",
  "buildApiPath",
  "buildApiUrl",
]);
const authenticatedApiCalls = new Set([
  "apiFetch",
  "downloadApiFile",
  "fetchApiFile",
]);

const listProductionSourceFiles = (directory: string): string[] =>
  readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      return listProductionSourceFiles(absolute);
    }
    if (
      !entry.name.match(/\.tsx?$/)
      || entry.name.endsWith(".d.ts")
      || entry.name.includes(".test.")
    ) {
      return [];
    }
    return [absolute];
  });

const toRelative = (file: string): string =>
  path.relative(sourceRoot, file).split(path.sep).join("/");

const isApiDefinitionModule = (relativeFile: string): boolean =>
  relativeFile.startsWith("lib/api/")
  || /^entities\/[^/]+\/api\//.test(relativeFile);

const containsApiRouteLiteral = (node: ts.Node): boolean => {
  let found = false;
  const visit = (child: ts.Node): void => {
    if (found) {
      return;
    }
    if (
      (ts.isStringLiteralLike(child) || ts.isTemplateHead(child) || ts.isTemplateMiddle(child))
      && apiRoutePattern.test(child.text)
    ) {
      found = true;
      return;
    }
    ts.forEachChild(child, visit);
  };
  visit(node);
  return found;
};

const propertyName = (node: ts.Expression): string | null => {
  if (ts.isPropertyAccessExpression(node)) {
    return node.name.text;
  }
  if (
    ts.isElementAccessExpression(node)
    && node.argumentExpression
    && ts.isStringLiteralLike(node.argumentExpression)
  ) {
    return node.argumentExpression.text;
  }
  return null;
};

const isInsideAuthenticatedApiCall = (node: ts.Node): boolean => {
  let current = node.parent;
  while (current) {
    if (
      ts.isCallExpression(current)
      && ts.isIdentifier(current.expression)
      && authenticatedApiCalls.has(current.expression.text)
    ) {
      return true;
    }
    if (ts.isStatement(current)) {
      return false;
    }
    current = current.parent;
  }
  return false;
};

export function findProtectedApiAccessViolations(
  relativeFile: string,
  sourceText: string,
): string[] {
  const sourceFile = ts.createSourceFile(
    relativeFile,
    sourceText,
    ts.ScriptTarget.Latest,
    true,
    relativeFile.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const violations = new Set<string>();
  const allowDirectFetch = relativeFile === "lib/api/client.ts";
  const allowLowLevelBlobFetch = relativeFile === "lib/api/download.ts";

  const report = (node: ts.Node, message: string): void => {
    const line = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;
    violations.add(`${relativeFile}:${line} ${message}`);
  };

  const visit = (node: ts.Node): void => {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      const isClientImport = node.moduleSpecifier.text === "@/lib/api/client"
        || node.moduleSpecifier.text.endsWith("/api/client");
      if (isClientImport && !allowLowLevelBlobFetch) {
        const namedBindings = node.importClause?.namedBindings;
        if (namedBindings && ts.isNamedImports(namedBindings)) {
          for (const element of namedBindings.elements) {
            const importedName = element.propertyName?.text ?? element.name.text;
            if (lowLevelClientExports.has(importedName)) {
              report(
                element,
                `must not import ${importedName}; use lib/api/download.ts for files`,
              );
            }
          }
        }
      }
    }

    if (ts.isCallExpression(node)) {
      const calledProperty = propertyName(node.expression);
      if (
        !allowDirectFetch
        && (
          (ts.isIdentifier(node.expression) && node.expression.text === "fetch")
          || calledProperty === "fetch"
        )
      ) {
        report(node, "must not call fetch directly; use the API client");
      }

      const expressionText = node.expression.getText(sourceFile);
      const isNavigationCall = expressionText === "window.open"
        || expressionText === "location.assign"
        || expressionText === "location.replace"
        || expressionText === "window.location.assign"
        || expressionText === "window.location.replace"
        || (
          calledProperty === "setAttribute"
          && node.arguments[0]
          && ts.isStringLiteralLike(node.arguments[0])
          && ["href", "src", "action"].includes(node.arguments[0].text)
        );
      if (isNavigationCall && node.arguments.some(containsApiRouteLiteral)) {
        report(node, "must not navigate directly to a protected API route");
      }
    }

    if (
      ts.isJsxAttribute(node)
      && ["href", "src", "action"].includes(node.name.getText(sourceFile))
      && node.initializer
      && containsApiRouteLiteral(node.initializer)
    ) {
      report(node, "must not render a protected API route as a navigation URL");
    }

    if (
      ts.isBinaryExpression(node)
      && node.operatorToken.kind === ts.SyntaxKind.EqualsToken
      && ["href", "src", "action", "location"].includes(propertyName(node.left) ?? "")
      && containsApiRouteLiteral(node.right)
    ) {
      report(node, "must not assign a protected API route to a navigation target");
    }

    if (
      node.parent !== undefined
      && !ts.isImportDeclaration(node.parent)
      && !ts.isExportDeclaration(node.parent)
      && (ts.isStringLiteralLike(node) || ts.isTemplateHead(node) || ts.isTemplateMiddle(node))
      && apiRoutePattern.test(node.text)
    ) {
      if (!isApiDefinitionModule(relativeFile)) {
        report(node, "must declare protected API routes inside an API module");
      } else if (!isInsideAuthenticatedApiCall(node)) {
        report(
          node,
          "must pass protected API routes directly to an authenticated API or download client",
        );
      }
    }

    ts.forEachChild(node, visit);
  };

  visit(sourceFile);
  return [...violations].sort();
}

describe("protected API access boundaries", () => {
  it("keeps production code behind the API and download clients", () => {
    const violations = listProductionSourceFiles(sourceRoot).flatMap((file) =>
      findProtectedApiAccessViolations(toRelative(file), readFileSync(file, "utf8")),
    );

    expect(violations).toEqual([]);
  });

  it("detects direct requests and protected navigation", () => {
    const source = `
      fetch('/api/export');
      window.open('/api/export');
      window.location.assign('/api/export');
      const link = document.createElement('a');
      link.href = '/api/export';
      const node = <a href="/api/export">download</a>;
    `;

    expect(findProtectedApiAccessViolations("pages/Unsafe.tsx", source)).toEqual(
      expect.arrayContaining([
        expect.stringContaining("must not call fetch directly"),
        expect.stringContaining("must not navigate directly"),
        expect.stringContaining("must not assign"),
        expect.stringContaining("must not render"),
      ]),
    );
  });

  it("detects low-level download imports outside the download boundary", () => {
    const source = `
      import { apiFetchBlob, buildApiPath } from '@/lib/api/client';
      export const download = () => apiFetchBlob(buildApiPath('/api/export'));
    `;

    expect(findProtectedApiAccessViolations("lib/api/unsafeDownload.ts", source)).toEqual(
      expect.arrayContaining([
        expect.stringContaining("must not import apiFetchBlob"),
        expect.stringContaining("must not import buildApiPath"),
      ]),
    );
  });

  it("rejects API modules that expose an unauthenticated download URL", () => {
    const source = `
      export const getDownloadUrl = () => '/api/export';
    `;

    expect(findProtectedApiAccessViolations("lib/api/unsafeDownload.ts", source)).toEqual([
      expect.stringContaining("must pass protected API routes directly"),
    ]);
  });
});
