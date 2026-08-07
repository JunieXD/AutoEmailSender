import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";


type CoverageAction = {
  source: string;
  exported_actions?: unknown;
  excluded_exports?: unknown;
};

type CoverageDocument = {
  schema_version: number;
  excluded_sources?: unknown;
  actions: CoverageAction[];
};

type ActionMismatch = {
  source: string;
  missing: string[];
  stale: string[];
};

type CoverageDrift = {
  duplicateSources: string[];
  missingSources: string[];
  unexpectedSources: string[];
  actionMismatches: ActionMismatch[];
};

const frontendRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const repositoryRoot = path.resolve(frontendRoot, "..");
const frontendSourceRoot = path.join(frontendRoot, "src");
const coverageFile = path.join(
  repositoryRoot,
  "docs",
  "development",
  "agent_cli_gui_coverage.json",
);

const normalizeRelativePath = (file: string): string =>
  path.relative(frontendSourceRoot, file).split(path.sep).join("/");

const stringValues = (value: unknown): string[] =>
  Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.length > 0)
    : [];

const objectKeys = (value: unknown): string[] =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? Object.keys(value)
    : [];

const hasModifier = (node: ts.Node, kind: ts.SyntaxKind): boolean =>
  ts.canHaveModifiers(node) && ts.getModifiers(node)?.some((modifier) => modifier.kind === kind) === true;

const collectBindingNames = (name: ts.BindingName, target: Set<string>): void => {
  if (ts.isIdentifier(name)) {
    target.add(name.text);
    return;
  }
  for (const element of name.elements) {
    if (!ts.isOmittedExpression(element)) collectBindingNames(element.name, target);
  }
};

const resolveReexport = (sourceFile: string, specifier: string): string | null => {
  let base: string;
  if (specifier.startsWith("@/")) {
    base = path.join(frontendSourceRoot, specifier.slice(2));
  } else if (specifier.startsWith(".")) {
    base = path.resolve(path.dirname(sourceFile), specifier);
  } else {
    return null;
  }

  const candidates = [
    base,
    `${base}.ts`,
    `${base}.tsx`,
    path.join(base, "index.ts"),
    path.join(base, "index.tsx"),
  ];
  return candidates.find((candidate) => existsSync(candidate)) ?? null;
};

const exportedValueNames = (
  file: string,
  visited: Set<string> = new Set(),
): Set<string> => {
  const resolvedFile = path.resolve(file);
  if (visited.has(resolvedFile)) return new Set();
  visited.add(resolvedFile);

  const sourceText = readFileSync(resolvedFile, "utf8");
  const sourceFile = ts.createSourceFile(
    resolvedFile,
    sourceText,
    ts.ScriptTarget.Latest,
    true,
    resolvedFile.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const names = new Set<string>();

  for (const statement of sourceFile.statements) {
    if (ts.isVariableStatement(statement) && hasModifier(statement, ts.SyntaxKind.ExportKeyword)) {
      for (const declaration of statement.declarationList.declarations) {
        collectBindingNames(declaration.name, names);
      }
      continue;
    }

    if (
      (ts.isFunctionDeclaration(statement) || ts.isClassDeclaration(statement))
      && hasModifier(statement, ts.SyntaxKind.ExportKeyword)
      && statement.name
    ) {
      names.add(statement.name.text);
      continue;
    }

    if (!ts.isExportDeclaration(statement)) continue;
    if (statement.exportClause && ts.isNamedExports(statement.exportClause)) {
      if (statement.isTypeOnly) continue;
      for (const element of statement.exportClause.elements) {
        if (!element.isTypeOnly) names.add(element.name.text);
      }
      continue;
    }

    if (!statement.exportClause && ts.isStringLiteral(statement.moduleSpecifier)) {
      const reexportedFile = resolveReexport(resolvedFile, statement.moduleSpecifier.text);
      if (!reexportedFile) {
        throw new Error(
          `Cannot resolve frontend re-export: ${normalizeRelativePath(resolvedFile)} -> ${statement.moduleSpecifier.text}`,
        );
      }
      for (const name of exportedValueNames(reexportedFile, visited)) names.add(name);
    }
  }

  return names;
};

const directTypeScriptFiles = (directory: string): string[] => {
  if (!existsSync(directory)) return [];
  return readdirSync(directory, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith(".ts") && !entry.name.endsWith(".test.ts"))
    .map((entry) => path.join(directory, entry.name));
};

const discoverBusinessApiSources = (): string[] => {
  const legacyApiFiles = directTypeScriptFiles(path.join(frontendSourceRoot, "lib", "api"));
  const entitiesRoot = path.join(frontendSourceRoot, "entities");
  const entityApiFiles = existsSync(entitiesRoot)
    ? readdirSync(entitiesRoot, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .flatMap((entry) => directTypeScriptFiles(path.join(entitiesRoot, entry.name, "api")))
    : [];
  return [...legacyApiFiles, ...entityApiFiles]
    .map(normalizeRelativePath)
    .sort();
};

const readCoverageDocument = (): CoverageDocument =>
  JSON.parse(readFileSync(coverageFile, "utf8")) as CoverageDocument;

const collectCoverageDrift = (document: CoverageDocument): CoverageDrift => {
  const excludedSources = new Set(stringValues(document.excluded_sources));
  const expectedSources = discoverBusinessApiSources()
    .filter((source) => !excludedSources.has(source));
  const bySource = new Map<string, CoverageAction>();
  const duplicateSources = new Set<string>();

  for (const action of document.actions) {
    if (bySource.has(action.source)) duplicateSources.add(action.source);
    bySource.set(action.source, action);
  }

  const expectedSourceSet = new Set(expectedSources);
  const actionMismatches = expectedSources.flatMap((source): ActionMismatch[] => {
    const action = bySource.get(source);
    if (!action) return [];
    const actual = exportedValueNames(path.join(frontendSourceRoot, source));
    const classified = new Set([
      ...stringValues(action.exported_actions),
      ...objectKeys(action.excluded_exports),
    ]);
    const missing = [...actual].filter((name) => !classified.has(name)).sort();
    const stale = [...classified].filter((name) => !actual.has(name)).sort();
    return missing.length > 0 || stale.length > 0
      ? [{ source, missing, stale }]
      : [];
  });

  return {
    duplicateSources: [...duplicateSources].sort(),
    missingSources: expectedSources.filter((source) => !bySource.has(source)),
    unexpectedSources: [...bySource.keys()]
      .filter((source) => !expectedSourceSet.has(source))
      .sort(),
    actionMismatches,
  };
};

describe("Agent CLI GUI coverage contract", () => {
  it("classifies every exported frontend business API action", () => {
    const document = readCoverageDocument();
    expect(document.schema_version).toBe(2);
    expect(collectCoverageDrift(document)).toEqual({
      duplicateSources: [],
      missingSources: [],
      unexpectedSources: [],
      actionMismatches: [],
    });
  });

  it("reports every missing classification in one failure", () => {
    const document = structuredClone(readCoverageDocument());
    const omittedBySource = new Map([
      ["lib/api/crawlJobsApi.ts", "getCrawlJobDetails"],
      [
        "entities/professor/api/informationEnrichment.ts",
        "listProfessorInformationEnrichmentItemsPage",
      ],
    ]);
    for (const action of document.actions) {
      const omitted = omittedBySource.get(action.source);
      if (omitted) {
        action.exported_actions = stringValues(action.exported_actions)
          .filter((name) => name !== omitted);
      }
    }

    expect(collectCoverageDrift(document).actionMismatches).toEqual([
      {
        source: "entities/professor/api/informationEnrichment.ts",
        missing: ["listProfessorInformationEnrichmentItemsPage"],
        stale: [],
      },
      {
        source: "lib/api/crawlJobsApi.ts",
        missing: ["getCrawlJobDetails"],
        stale: [],
      },
    ]);
  });
});
