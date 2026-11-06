/**
 * The one `chrome.scripting.executeScript` wrapper shared by the popup and
 * the background worker. Resolves to the injected function's return value
 * (`results[0].result`), rejects if the injection fails, and rejects after
 * `timeoutMs` so a hung page can never wedge a caller (e.g. leave a
 * `runOnce` key stuck in flight forever).
 */

export const EXEC_TIMEOUT_MS = 30000;

function scriptTarget(target) {
  if (target && typeof target === "object") {
    return target.documentId
      ? { tabId: target.tabId, documentIds: [target.documentId] }
      : { tabId: target.tabId };
  }
  return { tabId: target };
}

/** The top-frame documentId of `tabId` (Chrome 106+); undefined elsewhere. */
export async function topDocumentId(tabId) {
  try {
    const frame = await chrome.webNavigation?.getFrame?.({ tabId, frameId: 0 });
    return frame?.documentId || undefined;
  } catch {
    return undefined;
  }
}

/**
 * @param {number|{tabId: number, documentId?: string}} target a tab id, or a tab
 *   plus the `documentId` of the document the caller observed (Chrome): the
 *   injection then fails instead of running in whatever page the tab has
 *   navigated to since.
 * @param {Function} func self-contained function to run in the page
 * @param {unknown[]} [args]
 * @param {number} [timeoutMs]
 */
export async function execInTab(target, func, args = [], timeoutMs = EXEC_TIMEOUT_MS) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("Timed out reading the page.")), timeoutMs);
  });
  try {
    const results = await Promise.race([
      chrome.scripting.executeScript({ target: scriptTarget(target), func, args }),
      timeout,
    ]);
    return results && results[0] && results[0].result;
  } finally {
    clearTimeout(timer);
  }
}
