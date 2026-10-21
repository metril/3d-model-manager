import { sanitizeHtml } from "@/lib/sanitize";

const HAS_TAG = /<[a-z][^>]*>/i;

/** Imported descriptions (MakerWorld/Printables/Thingiverse) are HTML
 * fragments (`<p><strong>…`, `&amp;`); user-edited ones are plain text with
 * no markup at all. Sanitizes to a safe HTML fragment for read-mode
 * rendering -- DOMPurify's default allowlist; links are forced to open in
 * a new tab with a locked-down `rel` (see `lib/sanitize`). Plain-text input (no tags) has its line
 * breaks turned into `<br>` first, since a bare newline collapses in HTML. */
export function sanitizeDescriptionHtml(raw: string): string {
  const html = HAS_TAG.test(raw) ? raw : raw.replace(/\n/g, "<br>");
  return sanitizeHtml(html);
}

/** Strips markup down to plain text, collapsing whitespace -- for contexts
 * (card summaries, previews) that want a plain string rather than rendered
 * HTML. */
export function toPlainText(raw: string): string {
  // DOMPurify with an empty tag allowlist strips markup but re-escapes
  // entities in its string output (`&amp;` stays `&amp;`) -- parsing the
  // sanitized fragment and reading `textContent` decodes them.
  const stripped = sanitizeHtml(raw, { ALLOWED_TAGS: [], ALLOWED_ATTR: [] });
  const div = document.createElement("div");
  div.innerHTML = stripped;
  return (div.textContent ?? "").replace(/\s+/g, " ").trim();
}
