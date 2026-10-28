/**
 * "Send to printer" logic: pure over an injected `api` (the `createClient`
 * v2 methods), no `chrome.*`. Queues a model's single sliced file; anything
 * ambiguous is reported (never guessed) so the popup can explain it.
 */

/**
 * Picks the printer to queue on: an explicit/remembered id if given, else
 * the only printer the key can see, else `null` (unassigned) when the key
 * sees none, else `undefined` meaning "ambiguous, the user must choose".
 */
export function resolvePrinter(preferred, printers) {
  if (Number.isInteger(preferred)) {
    return preferred;
  }
  if (printers.length === 1) {
    return printers[0].id;
  }
  return printers.length === 0 ? null : undefined;
}

/**
 * @param {object} opts
 * @param {object} opts.api client with v2Printables/v2Printers/v2Enqueue
 * @param {number} opts.modelId
 * @param {number|null} [opts.printerId] explicit choice; resolved when null
 * @returns {Promise<{status: "queued"|"none"|"multiple"|"need-printer"|"error", message?: string, count?: number}>}
 */
export async function queueModel({ api, modelId, printerId = null }) {
  const files = await api.v2Printables(modelId);
  if (!files.ok) {
    return { status: "error", message: files.error || "Couldn't list the model's sliced files." };
  }
  const list = Array.isArray(files.data) ? files.data : [];
  if (list.length === 0) {
    return { status: "none" };
  }
  if (list.length > 1) {
    return { status: "multiple", count: list.length };
  }
  let target = Number.isInteger(printerId) ? printerId : null;
  if (target === null) {
    const printers = await api.v2Printers();
    if (!printers.ok) {
      return { status: "error", message: printers.error || "Couldn't list printers." };
    }
    target = resolvePrinter(null, Array.isArray(printers.data) ? printers.data : []);
    if (target === undefined) {
      return { status: "need-printer" };
    }
  }
  const [file] = list;
  const res = await api.v2Enqueue({ modelId, fileId: file.file_id, plate: file.plate, printerId: target });
  return res.ok ? { status: "queued" } : { status: "error", message: res.error || "Couldn't add to the queue." };
}
