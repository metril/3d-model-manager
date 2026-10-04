import createDOMPurify from "dompurify";

import { isHttpUrl } from "@/lib/url";

// One shared sanitizer instance (rather than hooking the DOMPurify singleton)
// so every rendered-HTML path gets the same link hardening. Off-site links in
// imported descriptions/notes open in a new tab with `rel` locked down against
// tabnabbing; in-app (relative / same-origin), anchor and mailto links are
// left alone. Content cannot set `target`/`rel` itself: they are not in the
// default attribute allowlist, and this hook overwrites them for external
// links anyway.
const purify = createDOMPurify(window);

const isExternalHref = (href: string): boolean => {
  if (!isHttpUrl(href)) return false;
  try {
    return new URL(href).origin !== window.location.origin;
  } catch {
    return false;
  }
};

purify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName !== "A") return;
  const href = node.getAttribute("href");
  if (href && isExternalHref(href)) {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  } else {
    node.removeAttribute("target");
  }
});

export const sanitizeHtml = (html: string, config?: Parameters<typeof purify.sanitize>[1]): string =>
  String(purify.sanitize(html, config));
