import { describe, expect, it } from "vitest";

import { plainText } from "@/lib/plainText";

describe("plainText", () => {
  it("decodes escaped and double-escaped html", () => {
    expect(plainText("&lt;p&gt;no ams.&lt;/p&gt;")).toBe("no ams.");
    expect(plainText("&amp;lt;p&amp;gt;a &amp;amp; b&amp;lt;/p&amp;gt;")).toBe("a & b");
    expect(plainText(undefined)).toBe("");
  });
});
