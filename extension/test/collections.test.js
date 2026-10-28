import { test } from "node:test";
import assert from "node:assert/strict";

import {
  collectionDetailPathnameFrom,
  extractFavoritesListFrom,
  extractHandle,
  findCollectionTitleIn,
  findDesignListIn,
  hasFavoritesList,
  mapDesignHits,
  matchCollectionLinks,
  parseCollectionDetailUrl,
} from "../src/collections.js";

/** Compares `{key, designs}` only, ignoring `rawLength`. */
function findNoLen(pageProps) {
  const r = findDesignListIn(pageProps);
  return r && { key: r.key, designs: r.designs };
}

/** Wraps a `favoritesList` array in the shape of a real `__NEXT_DATA__`
 * script tag's parsed JSON (`{props: {pageProps: {favoritesList}}}`). */
function nextData(favoritesList) {
  return { props: { pageProps: { favoritesList } } };
}

test("extractFavoritesListFrom: happy path -- maps ALL collections regardless of status, including private ones", () => {
  // Mirrors backend/tests/cassettes/makerworld_fixtures.py FAVORITES_LIST.
  // Unlike the backend's own SSR-scrape importer (which only ever sees
  // public collections), this is the user's OWN account syncing to their
  // OWN library manager -- private collections (status !== 1) must sync
  // too (live bug report: the user's collections are mostly private, and
  // the old status===1 filter dropped nearly all of them).
  const entries = extractFavoritesListFrom(
    nextData([
      {
        id: 2155987,
        title: "Default Collection",
        slug: "default-collection",
        isDefault: true,
        designCnt: 7,
        status: 1,
      },
      {
        id: 18925823,
        title: "ESP32",
        slug: "esp32",
        isDefault: false,
        designCnt: 9,
        status: 1,
      },
      {
        id: 1793275,
        title: "Trays",
        slug: "trays",
        isDefault: false,
        designCnt: 3,
        status: 2, // private -- INCLUDED now (F1 fix)
      },
    ]),
  );

  assert.deepEqual(entries, [
    { list_id: "2155987", title: "Default Collection", slug: "default-collection", count: 7, is_default: true },
    { list_id: "18925823", title: "ESP32", slug: "esp32", count: 9, is_default: false },
    { list_id: "1793275", title: "Trays", slug: "trays", count: 3, is_default: false },
  ]);
});

test("extractFavoritesListFrom: missing __NEXT_DATA__/props/pageProps/favoritesList at any depth returns []", () => {
  assert.deepEqual(extractFavoritesListFrom(null), []);
  assert.deepEqual(extractFavoritesListFrom(undefined), []);
  assert.deepEqual(extractFavoritesListFrom({}), []);
  assert.deepEqual(extractFavoritesListFrom({ props: {} }), []);
  assert.deepEqual(extractFavoritesListFrom({ props: { pageProps: {} } }), []);
  assert.deepEqual(extractFavoritesListFrom({ props: { pageProps: { favoritesList: null } } }), []);
  assert.deepEqual(
    extractFavoritesListFrom({ props: { pageProps: { favoritesList: "not an array" } } }),
    [],
  );
});

test("extractFavoritesListFrom: drops entries missing an id or a title", () => {
  const entries = extractFavoritesListFrom(
    nextData([
      { title: "No id", status: 1 },
      { id: 42, status: 1 }, // no title
      { id: 0, title: "Falsy id", status: 1 }, // id 0 is falsy -- dropped like the backend's `if not cid`
      { id: 99, title: "Kept", status: 1 },
    ]),
  );

  assert.deepEqual(entries, [{ list_id: "99", title: "Kept", slug: null, count: null, is_default: false }]);
});

test("extractFavoritesListFrom: defaults slug/count to null and is_default to false when absent", () => {
  const entries = extractFavoritesListFrom(nextData([{ id: 5, title: "Bare", status: 1 }]));

  assert.deepEqual(entries, [{ list_id: "5", title: "Bare", slug: null, count: null, is_default: false }]);
});

test("extractFavoritesListFrom: accepts the data-route shape too -- pageProps passed directly, not wrapped in props", () => {
  const entries = extractFavoritesListFrom({ favoritesList: [{ id: 5, title: "Bare", status: 2 }] });
  assert.deepEqual(entries, [{ list_id: "5", title: "Bare", slug: null, count: null, is_default: false }]);
});

test("hasFavoritesList: true when a favoritesList array is present (inline or route shape), even when empty", () => {
  assert.equal(hasFavoritesList(nextData([])), true);
  assert.equal(hasFavoritesList({ favoritesList: [] }), true);
  assert.equal(hasFavoritesList(nextData([{ id: 1, title: "A", status: 1 }])), true);
});

test("hasFavoritesList: false when favoritesList is missing/malformed at any depth", () => {
  assert.equal(hasFavoritesList(null), false);
  assert.equal(hasFavoritesList(undefined), false);
  assert.equal(hasFavoritesList({}), false);
  assert.equal(hasFavoritesList({ props: {} }), false);
  assert.equal(hasFavoritesList({ favoritesList: "not an array" }), false);
});

test("extractHandle: reads the handle from the /@handle/collections URL path segment when __NEXT_DATA__ has no recognizable handle field", () => {
  const handle = extractHandle({}, "https://makerworld.com/en/@Terminalfoo/collections");
  assert.equal(handle, "Terminalfoo");
});

test("extractHandle: URL parsing works with a further path segment or query string", () => {
  assert.equal(
    extractHandle(null, "https://makerworld.com/@Terminalfoo/collections/2155987"),
    "Terminalfoo",
  );
  assert.equal(
    extractHandle(undefined, "https://makerworld.com/en/@Terminalfoo/collections?tab=all"),
    "Terminalfoo",
  );
});

test("extractHandle: prefers the URL's /@handle/collections segment over any __NEXT_DATA__ guess (F2 fix -- the URL is the guaranteed-reliable source, isCollectionsPage already required this path shape; __NEXT_DATA__ handle fields are unverified guesses)", () => {
  const nextData = { props: { pageProps: { userInfo: { name: "FromNextData" } } } };
  assert.equal(
    extractHandle(nextData, "https://makerworld.com/en/@FromUrl/collections"),
    "FromUrl",
  );
});

test("extractHandle: falls back to __NEXT_DATA__ only when the URL can't be parsed", () => {
  const nextData = { props: { pageProps: { userInfo: { name: "FromNextData" } } } };
  assert.equal(extractHandle(nextData, "not a url"), "FromNextData");
});

test("extractHandle: falls back to __NEXT_DATA__ when the URL parses but doesn't match the /@handle/collections shape", () => {
  const nextData = { props: { pageProps: { profile: { name: "FromNextData" } } } };
  assert.equal(
    extractHandle(nextData, "https://makerworld.com/some/other/page"),
    "FromNextData",
  );
});

test("extractHandle: an unparseable URL and no usable __NEXT_DATA__ returns null", () => {
  assert.equal(extractHandle({}, "not a url"), null);
  assert.equal(extractHandle({}, "https://makerworld.com/some/other/page"), null);
});


test("mapDesignHits: happy path -- maps hits to push entries", () => {
  // Mirrors backend/tests/cassettes/makerworld_fixtures.py FAVORITE_DESIGNS.
  const items = mapDesignHits({
    hits: [
      {
        id: 2188414,
        title: "ESP32-C6-Zigbee Gehäuse",
        cover: "https://makerworld.bblmw.com/cover1.jpg",
        designCreator: { uid: 3279776322, name: "Jackstyle" },
      },
      {
        id: 2603954,
        title: "Housing for ESP32-C6 DevKitC",
        cover: "https://makerworld.bblmw.com/cover2.jpg",
        designCreator: { uid: 4268272778, name: "Javier Lorenzana" },
      },
    ],
    total: 2,
  });

  assert.deepEqual(items, [
    {
      external_id: "2188414",
      title: "ESP32-C6-Zigbee Gehäuse",
      url: "https://makerworld.com/models/2188414",
      author: "Jackstyle",
      thumbnail_url: "https://makerworld.bblmw.com/cover1.jpg",
    },
    {
      external_id: "2603954",
      title: "Housing for ESP32-C6 DevKitC",
      url: "https://makerworld.com/models/2603954",
      author: "Javier Lorenzana",
      thumbnail_url: "https://makerworld.bblmw.com/cover2.jpg",
    },
  ]);
});

test("mapDesignHits: missing/malformed hits at any depth returns []", () => {
  assert.deepEqual(mapDesignHits(null), []);
  assert.deepEqual(mapDesignHits(undefined), []);
  assert.deepEqual(mapDesignHits({}), []);
  assert.deepEqual(mapDesignHits({ hits: null }), []);
  assert.deepEqual(mapDesignHits({ hits: "not an array" }), []);
  assert.deepEqual(mapDesignHits({ hits: [] }), []);
});

test("mapDesignHits: drops entries missing an id or a title, defaults absent author/cover to null", () => {
  const items = mapDesignHits({
    hits: [
      { title: "No id" },
      { id: 42 }, // no title
      { id: 0, title: "Falsy id" }, // id 0 is falsy -- dropped
      { id: 99, title: "Kept, no creator or cover" },
    ],
  });

  assert.deepEqual(items, [
    {
      external_id: "99",
      title: "Kept, no creator or cover",
      url: "https://makerworld.com/models/99",
      author: null,
      thumbnail_url: null,
    },
  ]);
});

// findDesignListIn: tolerant discovery of a collection page's own
// design-list array (M10 Workstream, live-bug fix -- the field name
// carrying a named collection's items on its SSR data route wasn't
// captured live, since the old /api/v1 endpoint this replaces as the
// PRIMARY item source produced zero items on a real sync).

test("findDesignListIn: matches a known key ('designs') directly, no deep-scan needed", () => {
  const found = findNoLen({
    designs: [{ id: 1, title: "Item A" }, { id: 2, title: "Item B" }],
    unrelated: "noise",
  });
  assert.deepEqual(found, {
    key: "designs",
    designs: [{ id: 1, title: "Item A" }, { id: 2, title: "Item B" }],
  });
});

test("findDesignListIn: matches other known keys ('favoritesDesigns', 'list') in priority order", () => {
  assert.deepEqual(findNoLen({ favoritesDesigns: [{ id: 1, title: "A" }] }), {
    key: "favoritesDesigns",
    designs: [{ id: 1, title: "A" }],
  });
  assert.deepEqual(findNoLen({ list: [{ id: 1, name: "A" }] }), {
    key: "list",
    designs: [{ id: 1, name: "A" }],
  });
});

test("findDesignListIn: a known key with an empty array is still trusted (a genuinely-empty collection)", () => {
  assert.deepEqual(findNoLen({ designs: [] }), { key: "designs", designs: [] });
});

test("findDesignListIn: a known key whose array doesn't look design-shaped is skipped in favor of a later match", () => {
  const found = findNoLen({
    designs: ["not", "design", "shaped"],
    favoritesDesigns: [{ id: 1, title: "Real design" }],
  });
  assert.deepEqual(found, { key: "favoritesDesigns", designs: [{ id: 1, title: "Real design" }] });
});

test("findDesignListIn: deep-scan fallback -- a non-empty array of design-shaped objects under an unrecognized top-level key", () => {
  const found = findNoLen({
    someUnrecognizedField: [{ id: 42, title: "Found via deep-scan" }],
  });
  assert.deepEqual(found, {
    key: "someUnrecognizedField",
    designs: [{ id: 42, title: "Found via deep-scan" }],
  });
});

test("findDesignListIn: deep-scan fallback also looks one level into a nested plain object", () => {
  const found = findNoLen({
    result: { items: [{ id: 7, name: "Nested design" }] },
  });
  assert.deepEqual(found, { key: "result.items", designs: [{ id: 7, name: "Nested design" }] });
});

test("findDesignListIn: deep-scan requires shape validation AND non-emptiness -- an empty or non-design array under an unknown key is never trusted", () => {
  assert.equal(findNoLen({ someField: [] }), null);
  assert.equal(findNoLen({ someField: ["not", "designs"] }), null);
  assert.equal(findNoLen({ someField: [{ id: "not-a-number", title: "A" }] }), null);
  assert.equal(findNoLen({ someField: [{ id: 1 }] }), null); // no title/name
});

test("findDesignListIn: no match anywhere (named keys or deep-scan) returns null", () => {
  assert.equal(findNoLen({}), null);
  assert.equal(findNoLen(null), null);
  assert.equal(findNoLen(undefined), null);
  assert.equal(findNoLen({ unrelated: "noise", other: 42 }), null);
});

// F1 hardening: `findDesignListIn` must never mistake the collections LIST
// itself (`favoritesList`, `extractFavoritesListFrom`'s source) for a
// collection's own items array -- live-bug-adjacent risk: a collection data
// route response that also happened to carry `pageProps.favoritesList`
// would previously have been deep-scanned like any other array and, since a
// `favoritesList` entry carries a numeric `id` and a string `title` just
// like a design does, wrongly matched.

test("findDesignListIn: excludes favoritesList from the named-key/deep-scan search entirely -- pageProps carrying ONLY favoritesList (collection-shaped entries) is not-found", () => {
  const found = findNoLen({
    favoritesList: [
      { id: 2155987, title: "Default Collection", designCnt: 7, isDefault: true },
      { id: 18925823, title: "ESP32", designCnt: 9, isDefault: false },
    ],
  });
  assert.equal(found, null);
});

test("findDesignListIn: favoritesList is excluded even when its entries would otherwise LOOK design-shaped (no collection markers) -- the key-name exclusion alone is enough to refuse it", () => {
  const found = findNoLen({
    favoritesList: [{ id: 1, title: "Looks design-shaped but is really a collection entry" }],
  });
  assert.equal(found, null);
});

test("findDesignListIn: collection-shaped objects (designCnt/isDefault present) are rejected by the discriminator under ANY key, named or deep-scanned -- not just favoritesList", () => {
  assert.equal(findNoLen({ someUnrecognizedKey: [{ id: 1, title: "A", designCnt: 5 }] }), null);
  assert.equal(findNoLen({ designs: [{ id: 1, title: "A", isDefault: true }] }), null);
});

test("findDesignListIn: an empty generic 'list' key does not short-circuit the search -- a real design array elsewhere still wins", () => {
  const found = findNoLen({
    list: [],
    someUnrecognizedKey: [{ id: 1, title: "Real design" }],
  });
  assert.deepEqual(found, { key: "someUnrecognizedKey", designs: [{ id: 1, title: "Real design" }] });
});

test("findDesignListIn: an empty generic 'list' key alone (nothing better found) is NOT trusted -- unlike the specific 'designs'/'favoritesDesigns' keys", () => {
  assert.equal(findNoLen({ list: [] }), null);
});

// F4 hardening: an array of nothing but `null`s carries zero positive
// design-shape evidence and must never pass as design-shaped.

test("findDesignListIn: an array of all-null entries is never design-shaped, named key or deep-scan", () => {
  assert.equal(findNoLen({ designs: [null, null] }), null);
  assert.equal(findNoLen({ someUnrecognizedKey: [null, null] }), null);
});

// matchCollectionLinks / parseCollectionDetailUrl / collectionDetailPathnameFrom
// (M11): ground-truth collection-detail shape, ANCHOR-derived pathnames --
// a real logged-in browser capture confirmed the detail page is
// `https://makerworld.com/en/collections/18925823-esp32` (locale-prefixed,
// PLURAL "collections", NO `@handle` segment, optional `-slug` suffix) --
// this SUPERSEDES the earlier `{collectionsPathname}/{listId}` guess, which
// a real sync confirmed finds nothing.

const REAL_DETAIL_URL = "https://makerworld.com/en/collections/18925823-esp32";

test("matchCollectionLinks: matches the real ground-truth URL shape (absolute, locale + slug)", () => {
  const result = matchCollectionLinks([REAL_DETAIL_URL], ["18925823"]);
  assert.deepEqual([...result], [["18925823", "/en/collections/18925823-esp32"]]);
});

test("matchCollectionLinks: matches a relative href, no locale, no slug", () => {
  const result = matchCollectionLinks(["/collections/18925823"], ["18925823"]);
  assert.equal(result.get("18925823"), "/collections/18925823");
});

test("matchCollectionLinks: matches the singular '/collection/<id>' spelling too", () => {
  const result = matchCollectionLinks(["/en/collection/18925823-esp32"], ["18925823"]);
  assert.equal(result.get("18925823"), "/en/collection/18925823-esp32");
});

test("matchCollectionLinks: strips a query string and hash before matching", () => {
  const result = matchCollectionLinks(
    ["https://makerworld.com/en/collections/18925823-esp32?tab=info#top"],
    ["18925823"],
  );
  assert.equal(result.get("18925823"), "/en/collections/18925823-esp32");
});

test("matchCollectionLinks: matches with a trailing slash", () => {
  const result = matchCollectionLinks(["/en/collections/18925823-esp32/"], ["18925823"]);
  assert.equal(result.get("18925823"), "/en/collections/18925823-esp32");
});

test("matchCollectionLinks: does NOT match the bare index/list page (/@handle/collections, no id)", () => {
  const result = matchCollectionLinks(
    ["https://makerworld.com/en/@Terminalfoo/collections"],
    ["18925823"],
  );
  assert.equal(result.size, 0);
});

test("matchCollectionLinks: does NOT match a list page with an @handle segment, even with a trailing id-looking segment", () => {
  // The OLD, now-confirmed-wrong guess shape -- must not be resurrected by
  // accident.
  const result = matchCollectionLinks(
    ["https://makerworld.com/en/@Terminalfoo/collections/18925823"],
    ["18925823"],
  );
  assert.equal(result.size, 0);
});

test("matchCollectionLinks: ignores an id not in the requested listIds", () => {
  const result = matchCollectionLinks(["/en/collections/99999999-other"], ["18925823"]);
  assert.equal(result.size, 0);
});

test("matchCollectionLinks: ignores a foreign-origin absolute href even with a matching path shape", () => {
  const result = matchCollectionLinks(
    ["https://thingiverse.com/collections/18925823-esp32"],
    ["18925823"],
  );
  assert.equal(result.size, 0);
});

test("matchCollectionLinks: ignores non-matching hrefs (unrelated pages, malformed input)", () => {
  const result = matchCollectionLinks(
    ["/en/models/643408-foo", "", null, undefined, "not a url or path??"],
    ["18925823"],
  );
  assert.equal(result.size, 0);
});

test("matchCollectionLinks: dedupes -- the FIRST matching href for a given id wins", () => {
  const result = matchCollectionLinks(
    ["/en/collections/18925823-esp32", "/de/collections/18925823-esp32"],
    ["18925823"],
  );
  assert.equal(result.get("18925823"), "/en/collections/18925823-esp32");
});

test("matchCollectionLinks: matches multiple different ids independently", () => {
  const result = matchCollectionLinks(
    ["/en/collections/18925823-esp32", "/en/collections/2155987-default-collection"],
    ["18925823", "2155987"],
  );
  assert.equal(result.size, 2);
  assert.equal(result.get("18925823"), "/en/collections/18925823-esp32");
  assert.equal(result.get("2155987"), "/en/collections/2155987-default-collection");
});

test("matchCollectionLinks: empty hrefs/listIds return an empty Map", () => {
  assert.equal(matchCollectionLinks([], []).size, 0);
  assert.equal(matchCollectionLinks(null, null).size, 0);
});

test("parseCollectionDetailUrl: the real ground-truth URL -> {id, slug}", () => {
  assert.deepEqual(parseCollectionDetailUrl(REAL_DETAIL_URL), { id: "18925823", slug: "esp32" });
});

test("parseCollectionDetailUrl: no slug, no locale", () => {
  assert.deepEqual(parseCollectionDetailUrl("https://makerworld.com/collections/18925823"), {
    id: "18925823",
    slug: null,
  });
});

test("parseCollectionDetailUrl: returns null for the list/index page (/@handle/collections)", () => {
  assert.equal(
    parseCollectionDetailUrl("https://makerworld.com/en/@Terminalfoo/collections"),
    null,
  );
});

test("parseCollectionDetailUrl: returns null for an unrelated page or a malformed URL", () => {
  assert.equal(parseCollectionDetailUrl("https://makerworld.com/en/models/643408-foo"), null);
  assert.equal(parseCollectionDetailUrl("not a url"), null);
});

// M1 hardening: parseCollectionDetailUrl (unlike matchCollectionLinks, whose
// matches are pre-filtered to ids we already trust) must stay PLURAL-ONLY --
// tolerating the singular "collection" spelling here would let an unrelated
// future `/collection/{id}` route on the site get parsed and synced as if
// it were one of our collections.

test("parseCollectionDetailUrl: the singular '/collection/<id>' spelling no longer matches (M1)", () => {
  assert.equal(parseCollectionDetailUrl("https://makerworld.com/en/collection/18925823-esp32"), null);
  assert.equal(parseCollectionDetailUrl("https://makerworld.com/collection/18925823"), null);
});

test("collectionDetailPathnameFrom: builds the ground-truth shape with a locale and a slug", () => {
  assert.equal(
    collectionDetailPathnameFrom("https://makerworld.com/en/@Terminalfoo/collections", "18925823", "esp32"),
    "/en/collections/18925823-esp32",
  );
});

test("collectionDetailPathnameFrom: omits the slug when the entry has none", () => {
  assert.equal(
    collectionDetailPathnameFrom("https://makerworld.com/en/@Terminalfoo/collections", "18925823", null),
    "/en/collections/18925823",
  );
});

test("collectionDetailPathnameFrom: defaults to the en locale when the URL has none", () => {
  assert.equal(
    collectionDetailPathnameFrom("https://makerworld.com/@Terminalfoo/collections", "18925823", "esp32"),
    "/en/collections/18925823-esp32",
  );
});

test("collectionDetailPathnameFrom: falls back to en when the URL doesn't parse", () => {
  assert.equal(collectionDetailPathnameFrom("not a url", "18925823", "esp32"), "/en/collections/18925823-esp32");
});

// M2: the slug comes straight off MakerWorld's own page data, not something
// this extension controls -- it must be encoded before being glued into a
// pathname, or a space/`%`/`#`/`?` in it would corrupt the constructed
// route (a literal `#`/`?` would otherwise be read back as the
// fragment/query start instead of part of the path).

test("collectionDetailPathnameFrom: encodes special characters (space/percent/hash) in the slug", () => {
  assert.equal(
    collectionDetailPathnameFrom("https://makerworld.com/en/@Terminalfoo/collections", "18925823", "esp32 100% #1"),
    "/en/collections/18925823-esp32%20100%25%20%231",
  );
});

test("collectionDetailPathnameFrom: a plain slug (the real 18925823-esp32 fixture) is unchanged by encoding", () => {
  assert.equal(
    collectionDetailPathnameFrom("https://makerworld.com/en/@Terminalfoo/collections", "18925823", "esp32"),
    "/en/collections/18925823-esp32",
  );
});

// findCollectionTitleIn (M11): tolerant discovery of a collection DETAIL
// page's own title, mirroring `findDesignListIn`'s tolerant discovery of
// the items array -- UNVERIFIED field name, so tries known plausible keys
// first, then a shallow deep-scan. Returns `{title, count}` (I1 hardening:
// `count` is `syncCollectionDetail`'s only signal that a single-SSR-response
// item read might have been truncated -- see its own doc).

test("findCollectionTitleIn: matches a known key ('favoritesInfo') directly, count null when no designCnt/count field is present", () => {
  const info = findCollectionTitleIn({ favoritesInfo: { id: 18925823, title: "ESP32" } }, "18925823");
  assert.deepEqual(info, { title: "ESP32", count: null });
});

test("findCollectionTitleIn: derives count from designCnt when present", () => {
  const info = findCollectionTitleIn(
    { favoritesInfo: { id: 18925823, title: "ESP32", designCnt: 9 } },
    "18925823",
  );
  assert.deepEqual(info, { title: "ESP32", count: 9 });
});

test("findCollectionTitleIn: derives count from the more generic 'count' field when designCnt is absent", () => {
  const info = findCollectionTitleIn(
    { favoritesInfo: { id: 18925823, title: "ESP32", count: 4 } },
    "18925823",
  );
  assert.deepEqual(info, { title: "ESP32", count: 4 });
});

test("findCollectionTitleIn: designCnt is preferred over count when both are present", () => {
  const info = findCollectionTitleIn(
    { favoritesInfo: { id: 18925823, title: "ESP32", designCnt: 9, count: 4 } },
    "18925823",
  );
  assert.equal(info.count, 9);
});

test("findCollectionTitleIn: a non-numeric designCnt/count is not derived", () => {
  const info = findCollectionTitleIn(
    { favoritesInfo: { id: 18925823, title: "ESP32", designCnt: "9" } },
    "18925823",
  );
  assert.deepEqual(info, { title: "ESP32", count: null });
});

test("findCollectionTitleIn: deep-scan fallback under an unrecognized top-level key", () => {
  const info = findCollectionTitleIn(
    { someUnrecognizedField: { id: 18925823, title: "ESP32", designCnt: 2 } },
    "18925823",
  );
  assert.deepEqual(info, { title: "ESP32", count: 2 });
});

test("findCollectionTitleIn: deep-scan fallback one level into a nested plain object", () => {
  const info = findCollectionTitleIn(
    { result: { info: { id: 18925823, title: "ESP32" } } },
    "18925823",
  );
  assert.deepEqual(info, { title: "ESP32", count: null });
});

test("findCollectionTitleIn: never matches favoritesList (the collections LIST, not this one's own info)", () => {
  const info = findCollectionTitleIn(
    { favoritesList: [{ id: 18925823, title: "ESP32", designCnt: 9, isDefault: false }] },
    "18925823",
  );
  assert.equal(info, null);
});

test("findCollectionTitleIn: id mismatch is never matched, even with a matching title shape", () => {
  const info = findCollectionTitleIn({ favoritesInfo: { id: 111, title: "Wrong One" } }, "18925823");
  assert.equal(info, null);
});

test("findCollectionTitleIn: missing/malformed pageProps or id returns null", () => {
  assert.equal(findCollectionTitleIn(null, "18925823"), null);
  assert.equal(findCollectionTitleIn({}, "18925823"), null);
  assert.equal(findCollectionTitleIn({ favoritesInfo: { id: 18925823, title: "ESP32" } }, null), null);
});
