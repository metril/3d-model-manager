function hasControlChar(s: string): boolean {
  for (let i = 0; i < s.length; i++) if (s.charCodeAt(i) < 0x20) return true;
  return false;
}

/** Same-origin absolute path only (`/x?y`); anything else (absolute URLs,
 * protocol-relative `//host`, backslash tricks, the login page itself) -> null. */
export function safeRedirect(to: unknown): string | null {
  if (typeof to !== "string" || !to.startsWith("/") || to.startsWith("//") || to.includes("\\") || hasControlChar(to)) return null;
  if (/^\/login(?:[/?#]|$)/.test(to)) return null;
  return to;
}

/** `/login` URL that carries the page to return to after sign-in. */
export function loginUrlFor(returnTo: string): string {
  const safe = safeRedirect(returnTo);
  return safe && safe !== "/" ? `/login?redirect=${encodeURIComponent(safe)}` : "/login";
}
