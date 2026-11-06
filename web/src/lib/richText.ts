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
