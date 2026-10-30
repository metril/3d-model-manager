// Pure camera stall-detection helpers (kept out of the component file so
// react-refresh/only-export-components stays clean).

/** Consecutive stall reconnects before giving up to the offline state (its
 * 10 s auto-retry then takes over). */
export const MAX_RECONNECTS = 5;

/** Stalled when no frame has decoded for `stallMs` after connect, or the
 * decoded frame has been byte-identical for `stallMs`. */
export function isStreamStalled(
  naturalWidth: number,
  sinceConnectMs: number,
  sinceChangeMs: number,
  stallMs: number,
): boolean {
  if (naturalWidth === 0) return sinceConnectMs > stallMs;
  return sinceChangeMs > stallMs;
}

/** Reconnect delay after the n-th (0-based) consecutive stall: 5,10,20,40,60s. */
export function reconnectDelayMs(attempt: number): number {
  return Math.min(5_000 * 2 ** attempt, 60_000);
}

/** Exact byte comparison of two frame samples. */
export function framesEqual(a: Uint8ClampedArray, b: Uint8ClampedArray): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}
