import { describe, expect, it } from "vitest";

import { renderMarkdown } from "@/lib/markdown";
import { sanitizeHtml } from "@/lib/sanitize";

describe("sanitizeHtml", () => {
  it("strips javascript: hrefs", () => {
    const out = sanitizeHtml('<a href="javascript:alert(1)">x</a>');
    expect(out).not.toContain("javascript:");
  });

  it("forces target=_blank and a locked-down rel, overriding content-supplied values", () => {
    const out = sanitizeHtml('<a href="https://example.com" target="_self" rel="opener">x</a>');
    expect(out).toContain('target="_blank"');
    expect(out).toContain('rel="noopener noreferrer"');
    expect(out).not.toContain("_self");
    expect(out).not.toContain('rel="opener"');
  });

  it("leaves in-app, anchor and mailto links without target/rel", () => {
    for (const href of ["/models/x", "#notes", "mailto:a@b.c", `${window.location.origin}/models/y`]) {
      const out = sanitizeHtml(`<a href="${href}" target="_blank">x</a>`);
      expect(out).toContain(`href="${href}"`);
      expect(out).not.toContain("target=");
    }
  });

  it("applies the same hardening to rendered markdown links", () => {
    const out = renderMarkdown("[a](https://example.com)");
    expect(out).toContain('target="_blank"');
    expect(out).toContain('rel="noopener noreferrer"');
  });
});
