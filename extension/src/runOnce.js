/**
 * Per-key single-flight runner for background triggers. While a run for `key`
 * is in flight, further triggers don't start a second one -- they set a
 * dirty flag, and the run repeats ONCE more when it finishes, so a trigger
 * that arrives mid-run (e.g. a cookie change during a courier push) is never
 * lost. The in-flight key is always released, even if `fn` throws.
 */

const inFlight = new Set();
const dirty = new Set();

/**
 * @param {string} key
 * @param {() => Promise<unknown>|unknown} fn
 * @returns {Promise<void>} resolves when this trigger has been handled
 *   (immediately when it only marked an in-flight run dirty)
 */
export async function runOnce(key, fn) {
  if (inFlight.has(key)) {
    dirty.add(key);
    return;
  }
  inFlight.add(key);
  try {
    do {
      dirty.delete(key);
      await fn();
    } while (dirty.has(key));
  } finally {
    inFlight.delete(key);
    dirty.delete(key);
  }
}

/** Test helper: whether a run for `key` is currently in flight. */
export function isInFlight(key) {
  return inFlight.has(key);
}
