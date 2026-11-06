import { describe, expect, it } from "vitest";

import { sanitizeDescriptionHtml } from "@/lib/richText";

describe("sanitizeDescriptionHtml", () => {
  it("keeps safe formatting tags", () => {
    const out = sanitizeDescriptionHtml("<p><strong>Bold</strong> &amp; <em>italic</em></p>");
    expect(out).toContain("<strong>Bold</strong>");
    expect(out).toContain("&amp;");
    expect(out).toContain("<em>italic</em>");
  });

  it("strips scripts and event handlers", () => {
    const out = sanitizeDescriptionHtml('<p onclick="evil()">Hi</p><script>evil()</script>');
    expect(out).not.toContain("onclick");
    expect(out).not.toContain("<script>");
    expect(out).toContain("Hi");
  });

  it("turns plain-text line breaks into <br> without touching real HTML input", () => {
    expect(sanitizeDescriptionHtml("Line one\nLine two")).toBe("Line one<br>Line two");
    expect(sanitizeDescriptionHtml("<p>Line one\nLine two</p>")).toBe("<p>Line one\nLine two</p>");
  });

  it("preserves links and forces safe target/rel for new-tab opening", () => {
    const out = sanitizeDescriptionHtml('<a href="https://x.com">x</a>');
    expect(out).toContain('href="https://x.com"');
    expect(out).toContain('target="_blank"');
    expect(out).toContain('rel="noopener noreferrer"');
  });
});
