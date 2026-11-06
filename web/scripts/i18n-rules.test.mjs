import { describe, expect, it } from "vitest";
import { pluralProblems } from "./i18n-rules.mjs";

describe("pluralProblems", () => {
  it("accepts a complete pair", () => {
    expect(pluralProblems({ a: { n_one: "{{count}} x", n_other: "{{count}} xs" } })).toEqual([]);
  });
  it("requires both forms when either exists", () => {
    expect(pluralProblems({ n_one: "{{count}} x" })).toEqual(["n_one: missing n_other"]);
    expect(pluralProblems({ n_other: "{{count}} xs" })).toEqual(["n_other: missing n_one"]);
  });
  it("fails on {{count}} without plural forms", () => {
    expect(pluralProblems({ g: { n: "{{count}} xs" } })).toEqual(["g.n: {{count}} without _one/_other"]);
  });
  it("ignores strings without count", () => {
    expect(pluralProblems({ n: "hello" })).toEqual([]);
  });
});
