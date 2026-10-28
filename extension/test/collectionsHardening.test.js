import { test } from "node:test";
import assert from "node:assert/strict";

import {
  extractHandle,
  findDesignListIn,
  mapDesignHits,
  parseCollectionDetailUrl,
} from "../src/collections.js";

test("findDesignListIn: accepts numeric-string ids", () => {
  const found = findDesignListIn({ designs: [{ id: "123", title: "A" }] });
  assert.equal(found.designs.length, 1);
});

test("findDesignListIn: majority rule filters out bad items and reports the raw length", () => {
  const found = findDesignListIn({
    designs: [{ id: 1, title: "A" }, { id: 2, title: "B" }, { id: 3 }, null],
  });
  assert.deepEqual(
    found.designs.map((d) => d.id),
    [1, 2],
  );
  assert.equal(found.rawLength, 4);
});

test("findDesignListIn: a minority of design-shaped items is not a design list", () => {
  assert.equal(findDesignListIn({ designs: [{ id: 1, title: "A" }, "x", "y"] }), null);
});

test("extractHandle: a malformed %-escape in the handle doesn't throw", () => {
  assert.equal(extractHandle(null, "https://makerworld.com/en/@bad%ZZ/collections"), "bad%ZZ");
});

test("mapDesignHits: item urls use the locale-less /models/{id}", () => {
  const [item] = mapDesignHits({ hits: [{ id: 5, title: "T" }] });
  assert.equal(item.url, "https://makerworld.com/models/5");
});

test("parseCollectionDetailUrl: accepts looser locales (pt-br, zh-hant)", () => {
  assert.equal(parseCollectionDetailUrl("https://makerworld.com/pt-br/collections/9-x").id, "9");
  assert.equal(parseCollectionDetailUrl("https://makerworld.com/zh-hant/collections/9").id, "9");
});
