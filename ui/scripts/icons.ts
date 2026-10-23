// Regenerates crates/app/icons from docs/icon.svg. macOS icons keep the margin of Apple's icon grid
// (the shape is 824 of 1024 pixels); the other platforms use the full square.
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { run } from "@tauri-apps/cli";

const root = path.resolve(import.meta.dirname, "../..");
const source = path.join(root, "docs/icon.svg");
const work = path.join(root, "target/icons");
const out = path.join(root, "crates/app/icons");

const svg = readFileSync(source, "utf8").replace("<svg ", '<svg x="100" y="100" width="824" height="824" ');
mkdirSync(work, { recursive: true });
writeFileSync(
  path.join(work, "macos.svg"),
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">${svg}</svg>`,
);

await run(["icon", source, "-o", path.join(work, "full")]);
await run(["icon", path.join(work, "macos.svg"), "-o", path.join(work, "macos")]);
mkdirSync(out, { recursive: true });
for (const file of ["32x32.png", "128x128.png", "128x128@2x.png", "icon.ico"]) {
  copyFileSync(path.join(work, "full", file), path.join(out, file));
}
copyFileSync(path.join(work, "macos", "icon.icns"), path.join(out, "icon.icns"));
