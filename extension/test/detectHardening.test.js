import { test } from "node:test";
import assert from "node:assert/strict";

import {
  detectSite,
  isCollectionDetailPage,
  isCollectionsPage,
  isModelPage,
  siteEntryFor,
} from "../src/detect.js";

test("isModelPage: id patterns are matched on the pathname only, never the query/hash", () => {
  assert.equal(isModelPage("https://makerworld.com/en/search?q=/models/123"), false);
  assert.equal(isModelPage("https://makerworld.com/en#/models/123"), false);
  assert.equal(isModelPage("https://www.printables.com/search?x=model/123"), false);
});

test("isModelPage: patterns are anchored (no substring false positives)", () => {
  assert.equal(isModelPage("https://makerworld.com/en/foo/models/123"), false);
  assert.equal(isModelPage("https://makerworld.com/en/models/abc"), false);
  assert.equal(isModelPage("https://www.printables.com/en/user/model/123"), false);
  assert.equal(isModelPage("https://www.thingiverse.com/user/thing:123x"), false);
});

test("isModelPage: thing: forms and ?thing= query", () => {
  assert.equal(isModelPage("https://www.thingiverse.com/thing:4990415/files"), true);
  assert.equal(isModelPage("https://www.thingiverse.com/en/thing:4990415"), true);
  assert.equal(isModelPage("https://www.thingiverse.com/make:1?thing=77"), true);
  assert.equal(isModelPage("https://www.thingiverse.com/make:1?thing=abc"), false);
  assert.equal(isModelPage("https://www.thingiverse.com/make:1?other=77"), false);
});

test("isModelPage: looser locales (pt-br, zh-hant, three-letter)", () => {
  assert.equal(isModelPage("https://makerworld.com/pt-br/models/643408-foo"), true);
  assert.equal(isModelPage("https://makerworld.com/zh-hant/models/643408"), true);
  assert.equal(isModelPage("https://makerworld.com/fil/models/643408"), true);
  assert.equal(isModelPage("https://www.printables.com/pt-br/model/123-x"), true);
});

test("isCollectionsPage / isCollectionDetailPage: locale variants and non-matches", () => {
  assert.equal(isCollectionsPage("https://makerworld.com/pt-br/@bob/collections"), true);
  assert.equal(isCollectionsPage("https://www.thingiverse.com/@bob/collections"), false);
  assert.equal(isCollectionDetailPage("https://makerworld.com/zh-hant/collections/18925823-esp32"), true);
  assert.equal(isCollectionDetailPage("https://makerworld.com/en/@bob/collections"), false);
  assert.equal(isCollectionDetailPage("https://makerworld.com/en/collection/123"), false);
  assert.equal(isCollectionDetailPage("https://example.com/en/collections/123"), false);
});

test("siteEntryFor: single resolver for detectSite", () => {
  assert.equal(siteEntryFor("https://example.com/"), null);
  assert.equal(siteEntryFor("nope"), null);
  assert.equal(siteEntryFor("https://www.printables.com/x").entry.site, "printables");
  assert.equal(detectSite("https://www.printables.com/x"), "printables");
});
