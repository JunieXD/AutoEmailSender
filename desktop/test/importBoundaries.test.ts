import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";


const srcRoot = path.resolve(process.cwd(), "src");

const listSourceFiles = (directory: string): string[] =>
  readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) return listSourceFiles(absolute);
    if (!entry.name.endsWith(".ts") || entry.name.endsWith(".d.ts")) return [];
    return [absolute];
  });

const sourceFiles = listSourceFiles(srcRoot);
const knownFiles = new Set(sourceFiles.map((file) => path.resolve(file)));

const toRelative = (file: string): string =>
  path.relative(srcRoot, file).split(path.sep).join("/");

const importSpecifiers = (file: string): string[] => {
  const sourceText = readFileSync(file, "utf8");
  const sourceFile = ts.createSourceFile(
    file,
    sourceText,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS,
  );
  const imports: string[] = [];
  const visit = (node: ts.Node): void => {
    if (
      (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) &&
      node.moduleSpecifier &&
      ts.isStringLiteral(node.moduleSpecifier)
    ) {
      imports.push(node.moduleSpecifier.text);
    }
    if (
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

const resolveInternalImport = (source: string, specifier: string): string | null => {
  if (!specifier.startsWith(".")) return null;
  const unresolved = path.resolve(path.dirname(source), specifier);
  const withoutJsExtension = unresolved.replace(/\.js$/, "");
  const candidates = [
    unresolved,
    withoutJsExtension,
    `${withoutJsExtension}.ts`,
    path.join(withoutJsExtension, "index.ts"),
  ];
  return candidates.map((candidate) => path.resolve(candidate)).find((candidate) => knownFiles.has(candidate)) ?? null;
};

const dependencyGraph = (): Map<string, Set<string>> => {
  const graph = new Map<string, Set<string>>();
  for (const source of sourceFiles) {
    const sourceName = toRelative(source);
    const targets = new Set<string>();
    for (const specifier of importSpecifiers(source)) {
      const target = resolveInternalImport(source, specifier);
      if (target) targets.add(toRelative(target));
    }
    graph.set(sourceName, targets);
  }
  return graph;
};

const processBoundaryViolations = (graph: Map<string, Set<string>>): string[] => {
  const violations: string[] = [];
  for (const [source, targets] of graph) {
    for (const target of targets) {
      if (source !== "main.ts" && target === "main.ts") {
        violations.push(`${source} -> ${target}: only the entrypoint may own main-process composition`);
      }
      if (source !== "preload.ts" && target === "preload.ts") {
        violations.push(`${source} -> ${target}: preload must remain a process boundary`);
      }
      if (
        source === "main.ts" &&
        target !== "main/bootstrap/application.ts" &&
        target !== "main/packaged-qa/user-data.ts"
      ) {
        violations.push(`${source} -> ${target}: main entrypoint may only run the packaged QA gate and application bootstrap`);
      }
      if (source === "preload.ts" && target !== "preload/bridge.ts") {
        violations.push(`${source} -> ${target}: preload may only depend on bridge contracts`);
      }
      if (
        source.startsWith("preload/") &&
        !target.startsWith("contracts/") &&
        !target.startsWith("preload/")
      ) {
        violations.push(`${source} -> ${target}: preload modules may only depend on bridge contracts`);
      }
      if (source.startsWith("main/") && (target === "preload.ts" || target.startsWith("preload/"))) {
        violations.push(`${source} -> ${target}: main-process modules must not import preload code`);
      }
      if (
        source.startsWith("contracts/") &&
        (target === "main.ts" || target === "preload.ts" || target.startsWith("main/") || target.startsWith("preload/"))
      ) {
        violations.push(`${source} -> ${target}: contracts must not depend on process implementations`);
      }
      if (
        target.startsWith("main/") &&
        source !== "main.ts" &&
        !source.startsWith("main/")
      ) {
        violations.push(`${source} -> ${target}: only main-process code may use main-process modules`);
      }
    }
  }
  return violations.sort();
};

const importCycles = (graph: Map<string, Set<string>>): string[] => {
  const cycles = new Set<string>();
  const visiting = new Set<string>();
  const visited = new Set<string>();
  const stack: string[] = [];

  const visit = (node: string): void => {
    if (visiting.has(node)) {
      const start = stack.indexOf(node);
      cycles.add([...stack.slice(start), node].join(" -> "));
      return;
    }
    if (visited.has(node)) return;

    visiting.add(node);
    stack.push(node);
    for (const target of graph.get(node) ?? []) visit(target);
    stack.pop();
    visiting.delete(node);
    visited.add(node);
  };

  for (const node of graph.keys()) visit(node);
  return [...cycles].sort();
};

describe("desktop process and import boundaries", () => {
  it("keeps only stable process entrypoints at the source root", () => {
    const rootSourceFiles = sourceFiles
      .filter((file) => path.dirname(file) === srcRoot)
      .map(toRelative)
      .sort();

    expect(rootSourceFiles).toEqual(["main.ts", "preload.ts"]);
  });

  it("keeps main and preload dependencies pointed in the correct direction", () => {
    expect(processBoundaryViolations(dependencyGraph())).toEqual([]);
  });

  it("keeps the desktop source import graph acyclic", () => {
    expect(importCycles(dependencyGraph())).toEqual([]);
  });

  it("keeps the main entrypoint limited to bootstrap invocation", () => {
    const source = readFileSync(path.resolve(srcRoot, "main.ts"), "utf8");
    const sourceFile = ts.createSourceFile(
      "main.ts",
      source,
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TS,
    );

    expect(sourceFile.statements).toHaveLength(3);
    expect(ts.isImportDeclaration(sourceFile.statements[0])).toBe(true);
    expect(ts.isImportDeclaration(sourceFile.statements[1])).toBe(true);
    expect(ts.isTryStatement(sourceFile.statements[2])).toBe(true);
    expect(source).not.toContain('import { bootstrapDesktopApplication }');
    expect(source.indexOf("configurePackagedQaUserData(app)")).toBeLessThan(
      source.indexOf('import("./main/bootstrap/application.js")'),
    );
    expect(source).toContain("bootstrapDesktopApplication();");
    expect(source).toContain("app.exit(1);");
  });
});
