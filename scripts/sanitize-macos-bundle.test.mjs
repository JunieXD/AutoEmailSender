import assert from "node:assert/strict";
import test from "node:test";
import sanitizeMacBundleAfterSign, {
  assertNoCodeSigningExtendedAttributes,
  getMacApplicationBundlePath,
  sanitizeMacApplicationBundle,
} from "./sanitize-macos-bundle.mjs";

test("builds the macOS application bundle path from the packager context", () => {
  assert.equal(
    getMacApplicationBundlePath({
      appOutDir: "/tmp/release/mac-arm64",
      packager: { appInfo: { productFilename: "Auto Email Sender" } },
    }),
    "/tmp/release/mac-arm64/Auto Email Sender.app",
  );
});

test("clears extended attributes and verifies the signed macOS bundle", () => {
  const calls = [];
  const appPath = "/tmp/release/mac-arm64/Auto Email Sender.app";
  const execute = (...args) => {
    calls.push(args);
    return "";
  };

  sanitizeMacApplicationBundle(appPath, execute);

  assert.deepEqual(calls, [
    ["/usr/bin/xattr", ["-cr", appPath], { stdio: "inherit" }],
    [
      "/usr/bin/xattr",
      ["-r", appPath],
      { encoding: "utf8", maxBuffer: 10 * 1024 * 1024 },
    ],
    ["/usr/bin/codesign", ["--verify", "--deep", "--strict", appPath], { stdio: "inherit" }],
  ]);
});

test("rejects remaining code signing extended attributes", () => {
  const appPath = "/tmp/release/mac-arm64/Auto Email Sender.app";

  assert.throws(
    () =>
      assertNoCodeSigningExtendedAttributes(appPath, () =>
        `${appPath}: com.apple.cs.CodeDirectory\n`,
      ),
    /无法安全生成 Sparkle 差分包/,
  );
});

test("runs only for macOS after signing", async () => {
  const calls = [];

  await sanitizeMacBundleAfterSign(
    {
      electronPlatformName: "win32",
      appOutDir: "/tmp/release/win-unpacked",
      packager: { appInfo: { productFilename: "Auto Email Sender" } },
    },
    { execFileSync: (...args) => calls.push(args) },
  );

  assert.deepEqual(calls, []);
});
