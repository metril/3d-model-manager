import { describe, expect, it } from "vitest";

import { parseProfilesSearch } from "@/pages/profilesSearch";

describe("parseProfilesSearch", () => {
  it("defaults everything to undefined so a plain URL stays clean", () => {
    expect(parseProfilesSearch({})).toEqual({
      tab: undefined,
      printer: undefined,
      nozzle: undefined,
      layer: undefined,
      q: undefined,
      all: undefined,
    });
  });

  it("parses numbers from strings or numbers", () => {
    expect(parseProfilesSearch({ tab: "recipes", printer: "7", nozzle: "0.6", layer: 0.2, q: "pla", all: "1" })).toEqual({
      tab: "recipes",
      printer: 7,
      nozzle: 0.6,
      layer: 0.2,
      q: "pla",
      all: 1,
    });
  });

  it("drops invalid values", () => {
    const parsed = parseProfilesSearch({ tab: "nope", printer: "abc", nozzle: -1, layer: "0", q: "  ", all: "0" });
    expect(parsed).toEqual({
      tab: undefined,
      printer: undefined,
      nozzle: undefined,
      layer: undefined,
      q: undefined,
      all: undefined,
    });
    expect(parseProfilesSearch({ printer: 1.5 }).printer).toBeUndefined();
  });

  it("keeps the pipelines tab and treats profiles as the default (undefined)", () => {
    expect(parseProfilesSearch({ tab: "pipelines" }).tab).toBe("pipelines");
    expect(parseProfilesSearch({ tab: "profiles" }).tab).toBeUndefined();
  });
});
