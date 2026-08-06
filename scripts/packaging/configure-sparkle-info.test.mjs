import assert from "node:assert/strict";
import test from "node:test";
import { validateSparklePublicKey } from "./configure-sparkle-info.mjs";

test("accepts a canonical 32-byte Sparkle public key", () => {
  const key = Buffer.alloc(32, 7).toString("base64");

  assert.equal(validateSparklePublicKey(` ${key}\n`), key);
});

test("rejects placeholders and keys with the wrong length", () => {
  assert.throws(() => validateSparklePublicKey("${env.SPARKLE_PUBLIC_ED_KEY}"), /Base64/);
  assert.throws(
    () => validateSparklePublicKey(Buffer.alloc(31, 7).toString("base64")),
    /32 字节公钥/,
  );
});
