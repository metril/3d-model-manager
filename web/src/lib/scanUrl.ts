/** What a scanned QR points at: a spool label or a storage-location label. */
export type ScanTarget = { kind: "spool" | "location"; id: number };

/**
 * Parse the text of a scanned QR (a full `https://host/scan/spool/12` URL, a
 * bare `/scan/location/3` path, or a legacy `.../inventory/spools?spool=12`
 * link) into its target. Anything else is `null`.
 */
export function parseScanUrl(text: string): ScanTarget | null {
  const raw = text.trim();
  if (!raw) return null;
  let path = raw;
  let search = "";
  try {
    const u = new URL(raw, "http://scan.invalid");
    path = u.pathname;
    search = u.search;
  } catch {
    return null;
  }
  const m = /^\/scan\/(spool|location)\/(\d{1,15})\/?$/.exec(path);
  if (m) return { kind: m[1] as ScanTarget["kind"], id: Number(m[2]) };
  const legacy = /^\/inventory\/spools\/?$/.test(path) ? /[?&]spool=(\d{1,15})(?:&|$)/.exec(search) : null;
  if (legacy) return { kind: "spool", id: Number(legacy[1]) };
  return null;
}
