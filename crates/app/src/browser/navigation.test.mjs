import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

const script = readFileSync(new URL("./navigation.js", import.meta.url), "utf8");

function page({ api = true, topLevel = true } = {}) {
  const document = {};
  const navigation = api ? new EventTarget() : undefined;
  const window = { navigation };
  window.top = topLevel ? window : {};
  runInNewContext(script, { document, window });
  return { document, navigation };
}

function navigate(navigation, sameDocument = false) {
  const event = new Event("navigate");
  Object.defineProperty(event, "destination", { value: { sameDocument } });
  navigation.dispatchEvent(event);
}

test("tracks cross-document navigation initiated by page scripts", () => {
  const { document, navigation } = page();
  navigate(navigation);
  assert.equal(document.__aesCrawlLeaving, true);
});

test("intercepted navigation can settle after success", () => {
  const { document, navigation } = page();
  navigate(navigation);
  assert.equal(document.__aesCrawlLeaving, true);
  navigation.dispatchEvent(new Event("navigatesuccess"));
  assert.equal(document.__aesCrawlLeaving, false);
});

test("failed or cancelled navigation can settle after error", () => {
  const { document, navigation } = page();
  navigate(navigation);
  navigation.dispatchEvent(new Event("navigateerror"));
  assert.equal(document.__aesCrawlLeaving, false);
});

test("same-document navigation does not mark the document as leaving", () => {
  const { document, navigation } = page();
  navigate(navigation, true);
  assert.equal(document.__aesCrawlLeaving, false);
});

test("old WebKit without Navigation API keeps the document readable", () => {
  const { document } = page({ api: false });
  assert.equal(document.__aesCrawlLeaving, false);
});

test("child frames cannot install the top-level navigation tracker", () => {
  const { document, navigation } = page({ topLevel: false });
  navigate(navigation);
  assert.equal(document.__aesCrawlLeaving, undefined);
});
