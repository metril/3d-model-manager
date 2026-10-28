/**
 * Pure popup state functions (no DOM, no `chrome.*`) so the view selection
 * and the import-progress wording are unit-testable.
 */

import { isCollectionDetailPage, isCollectionsPage, isModelPage } from "./detect.js";

export const SUPPORTED_SITES = ["MakerWorld", "Thingiverse", "Printables"];

/**
 * Which popup screen to show.
 * @returns {"setup"|"model"|"collections"|"collection-detail"|"other"}
 */
export function popupView({ configured, url, hasTab = true }) {
  if (!configured) return "setup";
  if (!url) return "other";
  if (isModelPage(url)) return "model";
  if (hasTab && isCollectionsPage(url)) return "collections";
  if (hasTab && isCollectionDetailPage(url)) return "collection-detail";
  return "other";
}

const STATE_TEXT = {
  pending: "Waiting to start…",
  fetching: "Fetching model details…",
  downloading: "Downloading files…",
};

const QUEUE_TEXT = {
  queued: ["Added to the print queue ✓", "ok"],
  none: ["No sliced file yet — slice it in the app.", "warn"],
  "need-printer": ["Choose a printer to add it to the queue.", "warn"],
};

/**
 * @param {object|null} rec stored import record (see importTracker.js)
 * @param {{base: string}} ctx
 * @returns {{text: string, kind: "progress"|"ok"|"error"|"warn", done: boolean,
 *   openUrl: string|null, queueText: string|null, queueKind: string|null}}
 */
export function importView(rec, { base }) {
  const root = String(base ?? "").replace(/\/+$/, "");
  const view = { text: "", kind: "progress", done: false, openUrl: null, queueText: null, queueKind: null };
  if (!rec) return view;
  if (rec.state === "failed") {
    return { ...view, text: rec.error || "Import failed.", kind: "error" };
  }
  if (rec.gaveUp) {
    return { ...view, text: "Still importing — check the app.", kind: "warn", openUrl: root || null };
  }
  if (rec.state === "done") {
    view.done = true;
    view.kind = "ok";
    view.text = rec.deduped ? "Already in your library ✓" : "Imported ✓";
    view.openUrl = rec.modelId != null && root ? `${root}/models/${rec.modelId}` : null;
    const q = rec.queueResult;
    if (q?.status === "multiple") {
      [view.queueText, view.queueKind] = [`${q.count} sliced files — pick one in the app.`, "warn"];
    } else if (q?.status === "error") {
      [view.queueText, view.queueKind] = [q.message || "Couldn't add to the queue.", "error"];
    } else if (q && QUEUE_TEXT[q.status]) {
      [view.queueText, view.queueKind] = QUEUE_TEXT[q.status];
    }
    return view;
  }
  view.text = STATE_TEXT[rec.state] || "Importing…";
  return view;
}
