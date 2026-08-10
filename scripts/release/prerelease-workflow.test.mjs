import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const stableWorkflowPath = path.join(repositoryRoot, ".github", "workflows", "release.yml");
const prereleaseWorkflowPath = path.join(repositoryRoot, ".github", "workflows", "prerelease.yml");

test("the existing workflow dispatches a generic prerelease contract while retaining stable gates", async () => {
  const source = await readFile(stableWorkflowPath, "utf8");
  assert.match(source, /release_kind:\n[\s\S]*?default: stable/u);
  assert.match(source, /source_branch:/u);
  assert.match(source, /prerelease_channel:/u);
  assert.match(source, /uses: \.\/\.github\/workflows\/prerelease\.yml/u);
  assert.match(source, /inputs\.release_kind == 'prerelease'/u);
  assert.ok(
    (source.match(/inputs\.release_kind == 'stable'/gu) ?? []).length >= 5,
    "every stable build/certify/publish path must be gated to release_kind=stable",
  );
  assert.ok(
    (source.match(/!contains\(github\.ref_name, '-'\)/gu) ?? []).length >= 5,
    "prerelease tag pushes must not enter the stable release jobs",
  );
  assert.match(source, /\^v\[0-9\]\+\\\.\[0-9\]\+\\\.\[0-9\]\+\$/u);
  assert.doesNotMatch(source, /beta\/desktop-api-worker/u);
});

test("the reusable prerelease workflow builds once and publishes only exact manual installers", async () => {
  const source = await readFile(prereleaseWorkflowPath, "utf8");
  for (const input of [
    "release_tag",
    "release_sha",
    "source_branch",
    "prerelease_channel",
    "publish",
    "candidate_run_id",
  ]) {
    assert.match(source, new RegExp(`${input}:`));
  }
  assert.match(source, /test "\$GITHUB_REF_NAME" = "\$PRERELEASE_SOURCE_BRANCH"/u);
  assert.match(source, /git ls-remote --heads origin "refs\/heads\/\$PRERELEASE_SOURCE_BRANCH"/u);
  assert.match(source, /PRERELEASE_CANDIDATE_RUN_ID: \$\{\{ inputs\.candidate_run_id \}\}/u);
  assert.doesNotMatch(source, /run: \|[\s\S]*?"\$\{\{ inputs\.candidate_run_id \}\}"/u);
  assert.match(source, /prerelease-build-identity\.mjs/u);
  assert.match(source, /prerelease-candidate\.mjs candidate/u);
  assert.match(source, /name: prerelease-windows/u);
  assert.match(source, /name: prerelease-macos/u);
  assert.match(source, /name: prerelease-candidate/u);
  assert.match(source, /AutoEmailSender-Setup-\$version\.exe/u);
  assert.match(source, /AutoEmailSender-\$version-arm64\.dmg/u);
  assert.doesNotMatch(source, /prepare-sparkle-release|SPARKLE_ED_PRIVATE_KEY/u);
  assert.doesNotMatch(source, /--clobber/u);
  assert.doesNotMatch(source, /beta\/desktop-api-worker/u);
});

test("publish is non-Latest, immutable, and verifies stable isolation before and after", async () => {
  const source = await readFile(prereleaseWorkflowPath, "utf8");
  assert.match(source, /prerelease-isolation\.mjs capture/u);
  assert.match(source, /prerelease-isolation\.mjs compare/u);
  assert.match(source, /prerelease-isolation\.mjs verify/u);
  assert.match(source, /test -z "\$\(git ls-remote --tags origin/u);
  assert.match(source, /Release \$PRERELEASE_TAG already exists; publish a higher prerelease version/u);
  assert.match(source, /gh release create "\$PRERELEASE_TAG"[\s\S]*?--draft[\s\S]*?--prerelease[\s\S]*?--latest=false/u);
  assert.match(source, /gh release edit "\$PRERELEASE_TAG" --draft=false --prerelease --latest=false/u);
  assert.match(source, /Download and verify staged GitHub asset bytes/u);
  assert.match(source, /prerelease-isolation-verification\.json/u);
  assert.doesNotMatch(source, /--latest(?:\s|$)/mu);
});
