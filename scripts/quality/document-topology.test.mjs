import assert from "node:assert/strict";
import { access, readFile, readdir } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
);
const docsRoot = path.join(repositoryRoot, "docs");

async function collectMarkdownFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...await collectMarkdownFiles(entryPath));
    } else if (entry.isFile() && entry.name.endsWith(".md")) {
      files.push(entryPath);
    }
  }
  return files;
}

test("docs root is a map instead of a document dumping ground", async () => {
  const entries = await readdir(docsRoot, { withFileTypes: true });
  const rootFiles = entries
    .filter((entry) => entry.isFile())
    .map((entry) => entry.name)
    .sort();

  assert.deepEqual(rootFiles, ["README.md"]);
});

test("active and archived documentation have explicit owners", async () => {
  const expectedDirectories = [
    "architecture",
    "archive",
    "development",
    "operations",
    "product",
    "releases",
    "screenshots",
  ];
  const entries = await readdir(docsRoot, { withFileTypes: true });
  const directories = entries
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort();

  assert.deepEqual(directories, expectedDirectories);
  await readdir(path.join(docsRoot, "archive", "superpowers", "plans"));
  await readdir(path.join(docsRoot, "archive", "superpowers", "specs"));
});

test("CLI contract data is development-owned", async () => {
  const developmentEntries = await readdir(path.join(docsRoot, "development"));
  for (const fileName of [
    "agent_cli_baseline.json",
    "agent_cli_concurrency_coverage.json",
    "agent_cli_gui_coverage.json",
  ]) {
    assert.ok(developmentEntries.includes(fileName), `${fileName} must stay under development`);
  }
});

test("active Markdown local links resolve", async () => {
  const activeRoots = [
    path.join(docsRoot, "README.md"),
    ...await Promise.all(
      ["architecture", "development", "operations", "product", "releases"].map(
        (owner) => collectMarkdownFiles(path.join(docsRoot, owner)),
      ),
    ).then((groups) => groups.flat()),
  ];
  const markdownLink = /!?\[[^\]]*\]\(([^)]+)\)/g;

  for (const markdownPath of activeRoots) {
    const source = await readFile(markdownPath, "utf8");
    for (const match of source.matchAll(markdownLink)) {
      const target = match[1].trim().replace(/^<|>$/g, "");
      if (/^(?:[a-z]+:|#)/i.test(target)) {
        continue;
      }
      const fileTarget = decodeURIComponent(target.split("#", 1)[0]);
      await access(path.resolve(path.dirname(markdownPath), fileTarget));
    }
  }
});
