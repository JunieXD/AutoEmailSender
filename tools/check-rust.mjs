import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, readdirSync, rmSync, utimesSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { isDeepStrictEqual } from "node:util";

const root = fileURLToPath(new URL("../", import.meta.url));
function cargo(args, env = process.env, capture = false) {
  const result = spawnSync("cargo", args, {
    cwd: root,
    env,
    encoding: "utf8",
    stdio: capture ? ["ignore", "pipe", "inherit"] : "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
  return result.stdout;
}

const metadata = JSON.parse(cargo(["metadata", "--no-deps", "--format-version=1"], process.env, true));
const generated = join(metadata.target_directory, "sqlx-check");
rmSync(generated, { recursive: true, force: true });
mkdirSync(generated, { recursive: true });

// Like SQLx CLI, touch target roots so even an unchanged project expands every query macro.
// Stable Rust does not track the environment read by these macros for incremental compilation.
const now = new Date();
const sources = new Set(metadata.packages.flatMap((p) => p.targets.map((t) => t.src_path)));
for (const source of sources) utimesSync(source, now, now);

cargo(["clippy", "--workspace", "--all-targets", "--", "-D", "warnings"], {
  ...process.env,
  SQLX_OFFLINE: "false",
  SQLX_OFFLINE_DIR: generated,
  DATABASE_URL: "sqlite:target/sqlx.db",
});

const prepared = join(root, ".sqlx");
const queryFiles = (dir) => readdirSync(dir).filter((name) => /^query-.*\.json$/.test(name)).sort();
const expected = queryFiles(prepared);
const actual = queryFiles(generated);
if (!isDeepStrictEqual(expected, actual)) {
  console.error(".sqlx 中的查询文件有缺失或多余，运行 pnpm sqlx 重新生成");
  process.exit(1);
}
for (const name of actual) {
  const read = (dir) => JSON.parse(readFileSync(join(dir, name), "utf8"));
  if (!isDeepStrictEqual(read(prepared), read(generated))) {
    console.error(`.sqlx/${name} 不是最新的，运行 pnpm sqlx 重新生成`);
    process.exit(1);
  }
}
console.log(`已核对全部 ${actual.length} 个查询元数据文件`);
