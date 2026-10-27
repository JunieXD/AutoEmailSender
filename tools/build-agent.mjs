import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
function run(command, args, capture = false) {
  const result = spawnSync(command, args, {
    cwd: root,
    encoding: "utf8",
    stdio: capture ? ["ignore", "pipe", "inherit"] : "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
  return result.stdout?.trim();
}

const target = process.env.TAURI_ENV_TARGET_TRIPLE ?? run("rustc", ["--print", "host-tuple"], true);
const debug = process.argv.includes("--debug") || process.env.TAURI_ENV_DEBUG === "true";
const metadata = JSON.parse(run("cargo", ["metadata", "--no-deps", "--format-version=1"], true));
const args = ["build", "--package", "agent", "--bin", "autoemail", "--target", target];
if (!debug) args.push("--release");
run("cargo", args);
const extension = target.includes("windows") ? ".exe" : "";
const source = join(metadata.target_directory, target, debug ? "debug" : "release", `autoemail${extension}`);
const destination = join(root, "crates/app/binaries", `autoemail-${target}${extension}`);
mkdirSync(dirname(destination), { recursive: true });
copyFileSync(source, destination);
