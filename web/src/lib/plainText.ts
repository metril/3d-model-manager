const cache = new Map<string, string>();
const CACHE_MAX = 500;

/** Decode entity-escaped HTML to plain text (no innerHTML; DOMParser is inert).
 * Memoised: lists re-render with the same few descriptions/authors. */
export function plainText(input: string | null | undefined): string {
  if (!input) return "";
  const hit = cache.get(input);
  if (hit !== undefined) return hit;
  const out = decode(input);
  if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value as string);
  cache.set(input, out);
  return out;
}

function decode(input: string): string {
  let text = input;
  for (let i = 0; i < 3; i++) {
    const next = new DOMParser().parseFromString(text, "text/html").body.textContent ?? "";
    if (next === text) break;
    text = next;
  }
  return text.replace(/\s+/g, " ").trim();
}
