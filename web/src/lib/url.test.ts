import { describe, expect, it } from "vitest";

import { isHttpUrl } from "@/lib/url";

describe("isHttpUrl", () => {
  it("accepts http and https URLs", () => {
    expect(isHttpUrl("http://192.168.1.5:8080/stream")).toBe(true);
    expect(isHttpUrl("https://example.com/cam")).toBe(true);
  });

  it("rejects dangerous schemes, relative and empty values", () => {
    expect(isHttpUrl("javascript:alert(1)")).toBe(false);
    expect(isHttpUrl("data:text/html,<script>1</script>")).toBe(false);
    expect(isHttpUrl("/relative/path")).toBe(false);
    expect(isHttpUrl("")).toBe(false);
    expect(isHttpUrl(null)).toBe(false);
    expect(isHttpUrl(undefined)).toBe(false);
  });
});
