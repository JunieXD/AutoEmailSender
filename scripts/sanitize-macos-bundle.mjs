import { execFileSync } from "node:child_process";
import path from "node:path";

const XATTR_PATH = "/usr/bin/xattr";
const CODESIGN_PATH = "/usr/bin/codesign";

export function getMacApplicationBundlePath(context) {
  const appName = context.packager.appInfo.productFilename;
  return path.join(context.appOutDir, `${appName}.app`);
}

export function assertNoCodeSigningExtendedAttributes(appPath, execute = execFileSync) {
  const attributes = execute(XATTR_PATH, ["-r", appPath], {
    encoding: "utf8",
    maxBuffer: 10 * 1024 * 1024,
  });
  const codeSigningAttributes = attributes
    .split("\n")
    .filter((line) => line.includes(": com.apple.cs."));

  if (codeSigningAttributes.length > 0) {
    throw new Error(
      `macOS 应用包仍有代码签名扩展属性，无法安全生成 Sparkle 差分包：${codeSigningAttributes.join(", ")}`,
    );
  }
}

export function sanitizeMacApplicationBundle(appPath, execute = execFileSync) {
  execute(XATTR_PATH, ["-cr", appPath], { stdio: "inherit" });
  assertNoCodeSigningExtendedAttributes(appPath, execute);
  execute(CODESIGN_PATH, ["--verify", "--deep", "--strict", appPath], { stdio: "inherit" });
}

export default async function sanitizeMacBundleAfterSign(context, dependencies = {}) {
  if (context.electronPlatformName !== "darwin") {
    return;
  }

  sanitizeMacApplicationBundle(
    getMacApplicationBundlePath(context),
    dependencies.execFileSync ?? execFileSync,
  );
}
