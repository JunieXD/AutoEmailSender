import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";


const srcRoot = path.resolve(process.cwd(), "src");

const reviewedLegacyViolations = new Set([
  "context/BackgroundTaskNotificationContext.tsx -> features/crawl-review/client/crawlJobEvents.ts",
  "context/NotificationContext.tsx -> components/organisms/NotificationViewport.tsx",
  "lib/api/createTask.ts -> features/create-task/types.ts",
  "lib/api/tokenUsage.ts -> features/token-usage/client/tokenUsage.ts",
  "lib/useConfirmDialog.tsx -> components/atoms/ConfirmDialog.tsx",
]);

type Boundary = {
  layer: string;
  slice?: string;
};

const listSourceFiles = (directory: string): string[] =>
  readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) return listSourceFiles(absolute);
    if (!entry.name.match(/\.tsx?$/) || entry.name.endsWith(".d.ts")) return [];
    return [absolute];
  });

const sourceFiles = listSourceFiles(srcRoot);
const knownFiles = new Set(sourceFiles.map((file) => path.resolve(file)));

const toRelative = (file: string): string =>
  path.relative(srcRoot, file).split(path.sep).join("/");

const classify = (file: string): Boundary => {
  const [first, second] = toRelative(file).split("/");
  if (["shared", "entities", "features", "widgets", "pages"].includes(first)) {
    return { layer: first, slice: second };
  }
  if (first === "lib") return { layer: second === "api" ? "legacy-lib-api" : "legacy-lib" };
  if (first === "context") return { layer: "legacy-context" };
  if (first === "components") {
    return { layer: second === "atoms" ? "legacy-atoms" : "legacy-components" };
  }
  if (["App.tsx", "main.tsx"].includes(first)) return { layer: "app" };
  return { layer: "legacy-other" };
};

const importSpecifiers = (file: string): string[] => {
  const sourceText = readFileSync(file, "utf8");
  const sourceFile = ts.createSourceFile(
    file,
    sourceText,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const imports: string[] = [];
  const visit = (node: ts.Node): void => {
    if (
      (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) &&
      node.moduleSpecifier &&
      ts.isStringLiteral(node.moduleSpecifier)
    ) {
      imports.push(node.moduleSpecifier.text);
    } else if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword &&
      node.arguments.length === 1 &&
      ts.isStringLiteral(node.arguments[0])
    ) {
      imports.push(node.arguments[0].text);
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return imports;
};

const isPureReExportCompatibilityModule = (file: string): boolean => {
  const sourceText = readFileSync(file, "utf8");
  const sourceFile = ts.createSourceFile(
    file,
    sourceText,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  return (
    sourceFile.statements.length > 0 &&
    sourceFile.statements.every(
      (statement) =>
        ts.isExportDeclaration(statement) &&
        statement.moduleSpecifier !== undefined &&
        ts.isStringLiteral(statement.moduleSpecifier),
    )
  );
};

const resolveInternalImport = (source: string, specifier: string): string | null => {
  let unresolved: string;
  if (specifier.startsWith("@/")) unresolved = path.join(srcRoot, specifier.slice(2));
  else if (specifier.startsWith(".")) unresolved = path.resolve(path.dirname(source), specifier);
  else return null;

  const withoutJsExtension = unresolved.replace(/\.js$/, "");
  const candidates = [
    unresolved,
    withoutJsExtension,
    `${withoutJsExtension}.ts`,
    `${withoutJsExtension}.tsx`,
    path.join(withoutJsExtension, "index.ts"),
    path.join(withoutJsExtension, "index.tsx"),
  ];
  return candidates.map((candidate) => path.resolve(candidate)).find((candidate) => knownFiles.has(candidate)) ?? null;
};

const isForbidden = (source: Boundary, target: Boundary): boolean => {
  if (source.layer === "shared") {
    return ["entities", "features", "widgets", "pages", "app"].includes(target.layer);
  }
  if (source.layer === "entities") {
    return ["features", "widgets", "pages", "app"].includes(target.layer);
  }
  if (source.layer === "features") {
    return (
      ["widgets", "pages", "app"].includes(target.layer) ||
      (target.layer === "features" && source.slice !== target.slice)
    );
  }
  if (source.layer === "widgets") {
    return (
      ["pages", "app"].includes(target.layer) ||
      (target.layer === "widgets" && source.slice !== target.slice)
    );
  }
  if (source.layer === "legacy-lib" || source.layer === "legacy-lib-api") {
    return [
      "features",
      "entities",
      "widgets",
      "pages",
      "app",
      "legacy-context",
      "legacy-atoms",
      "legacy-components",
    ].includes(target.layer);
  }
  if (source.layer === "legacy-context") {
    return [
      "features",
      "entities",
      "widgets",
      "pages",
      "legacy-atoms",
      "legacy-components",
    ].includes(target.layer);
  }
  if (source.layer === "legacy-atoms") {
    return [
      "features",
      "entities",
      "widgets",
      "pages",
      "app",
      "legacy-context",
      "legacy-components",
    ].includes(target.layer);
  }
  return false;
};

const collectViolations = (): Set<string> => {
  const violations = new Set<string>();
  for (const source of sourceFiles) {
    for (const specifier of importSpecifiers(source)) {
      const target = resolveInternalImport(source, specifier);
      if (!target) continue;
      const sourceBoundary = classify(source);
      const targetBoundary = classify(target);
      const isVerifiedCompatibilityEdge =
        sourceBoundary.layer === "legacy-lib-api" &&
        targetBoundary.layer === "entities" &&
        isPureReExportCompatibilityModule(source);
      if (isForbidden(sourceBoundary, targetBoundary) && !isVerifiedCompatibilityEdge) {
        violations.add(`${toRelative(source)} -> ${toRelative(target)}`);
      }
    }
  }
  return violations;
};

describe("frontend architecture boundaries", () => {
  it("matches the reviewed legacy dependency baseline", () => {
    const actual = collectViolations();
    const newViolations = [...actual].filter((edge) => !reviewedLegacyViolations.has(edge)).sort();
    const staleAllowances = [...reviewedLegacyViolations].filter((edge) => !actual.has(edge)).sort();

    expect({ newViolations, staleAllowances }).toEqual({
      newViolations: [],
      staleAllowances: [],
    });
  });
});
