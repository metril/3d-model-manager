/** Decode entity-escaped HTML to plain text (no innerHTML; DOMParser is inert). */
export function plainText(input: string | null | undefined): string {
  let text = input ?? "";
  for (let i = 0; i < 3; i++) {
    const next = new DOMParser().parseFromString(text, "text/html").body.textContent ?? "";
    if (next === text) break;
    text = next;
  }
  return text.replace(/\s+/g, " ").trim();
}
