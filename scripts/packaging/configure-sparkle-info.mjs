import { execFileSync } from "node:child_process";
import path from "node:path";

export function validateSparklePublicKey(value) {
  const normalized = value.trim();
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(normalized)) {
    throw new Error("SPARKLE_PUBLIC_ED_KEY 不是有效的 Base64 字符串。");
  }

  const decoded = Buffer.from(normalized, "base64");
  if (decoded.length !== 32 || decoded.toString("base64") !== normalized) {
    throw new Error("SPARKLE_PUBLIC_ED_KEY 必须是 Sparkle Ed25519 的 32 字节公钥。");
  }
  return normalized;
}

export default async function configureSparkleInfo(context) {
  if (context.electronPlatformName !== "darwin") {
    return;
  }

  const rawPublicKey = process.env.SPARKLE_PUBLIC_ED_KEY;
  if (!rawPublicKey) {
    throw new Error("macOS 打包缺少 SPARKLE_PUBLIC_ED_KEY。");
  }
  const publicKey = validateSparklePublicKey(rawPublicKey);
  const appName = context.packager.appInfo.productFilename;
  const infoPlistPath = path.join(
    context.appOutDir,
    `${appName}.app`,
    "Contents",
    "Info.plist",
  );

  execFileSync(
    "/usr/bin/plutil",
    ["-insert", "SUPublicEDKey", "-string", publicKey, infoPlistPath],
    { stdio: "inherit" },
  );
  const writtenPublicKey = execFileSync(
    "/usr/bin/plutil",
    ["-extract", "SUPublicEDKey", "raw", infoPlistPath],
    { encoding: "utf8" },
  ).trim();
  if (writtenPublicKey !== publicKey) {
    throw new Error("写入 macOS Info.plist 的 Sparkle 公钥校验失败。");
  }
}
