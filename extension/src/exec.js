/**
 * The one `chrome.scripting.executeScript` wrapper shared by the popup and
 * the background worker. Resolves to the injected function's return value
 * (`results[0].result`), rejects if the injection fails, and rejects after
 * `timeoutMs` so a hung page can never wedge a caller (e.g. leave a
 * `runOnce` key stuck in flight forever).
 */

export const EXEC_TIMEOUT_MS = 30000;

/**
 * @param {number} tabId
 * @param {Function} func self-contained function to run in the page
 * @param {unknown[]} [args]
 * @param {number} [timeoutMs]
 */
export async function execInTab(tabId, func, args = [], timeoutMs = EXEC_TIMEOUT_MS) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("Timed out reading the page.")), timeoutMs);
  });
  try {
    const results = await Promise.race([
      chrome.scripting.executeScript({ target: { tabId }, func, args }),
      timeout,
    ]);
    return results && results[0] && results[0].result;
  } finally {
    clearTimeout(timer);
  }
}
