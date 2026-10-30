import { describe, expect, it } from "vitest";

import { parseLibrarySearch } from "@/pages/librarySearch";

describe("parseLibrarySearch", () => {
  // TanStack's default search parser JSON.parses each value, so `?collection=5`
  // already arrives as the number 5 -- the common case, passed straight through.
  it("passes a numeric collection id through", () => {
    expect(parseLibrarySearch({ collection: 5 })).toEqual({ collection: 5 });
  });

  it("drops a missing collection param to undefined", () => {
    expect(parseLibrarySearch({})).toEqual({ collection: undefined });
  });

  // A hand-edited or stale link (`?collection=abc`, or JSON like `?collection={}`)
  // must not leak through as a bogus filter value.
  it("drops a non-numeric collection value to undefined", () => {
    expect(parseLibrarySearch({ collection: "abc" })).toEqual({ collection: undefined });
    expect(parseLibrarySearch({ collection: { nope: 1 } })).toEqual({ collection: undefined });
    expect(parseLibrarySearch({ collection: null })).toEqual({ collection: undefined });
  });

  it("passes a numeric category id through", () => {
    expect(parseLibrarySearch({ category: 3 })).toEqual({ category: 3 });
  });

  it("drops a non-numeric category value to undefined", () => {
    expect(parseLibrarySearch({ category: "abc" })).toEqual({ category: undefined });
  });

  it("passes a string path through", () => {
    expect(parseLibrarySearch({ path: "figures/dnd" })).toEqual({ path: "figures/dnd" });
  });

  it("drops a non-string path value to undefined", () => {
    expect(parseLibrarySearch({ path: 5 })).toEqual({ path: undefined });
  });
});

describe("parseLibrarySearch filters", () => {
  it("keeps valid filter params and drops malformed ones", () => {
    const out = parseLibrarySearch({ q: 123, tags: [1, "x", 2], tagMode: "all", format: ["stl"], fav: true, sliced: false, sort: "name" });
    expect(out).toMatchObject({ q: "123", tags: [1, 2], tagMode: "all", format: ["stl"], fav: true, sort: "name" });
    expect(out.sliced).toBeUndefined();
    expect(parseLibrarySearch({ tagMode: "bogus", tags: "no" }).tagMode).toBeUndefined();
  });
});
