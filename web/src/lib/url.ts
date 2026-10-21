/** True only for absolute `http:`/`https:` URLs -- guards `href`s built from
 * server/device-supplied strings against `javascript:`/`data:` schemes. */
export function isHttpUrl(value: string | null | undefined): value is string {
  if (!value) return false;
  try {
    const { protocol } = new URL(value);
    return protocol === "http:" || protocol === "https:";
  } catch {
    return false;
  }
}
