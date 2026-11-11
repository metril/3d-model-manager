/**
 * Global "layout locked" flag shared by the docks (model detail, print
 * dashboard) and the stats grid. Locked by default; unlocking is an explicit
 * "Edit layout" action. Persisted to localStorage; every access is guarded.
 */
import { useSyncExternalStore } from "react";

export const LAYOUT_LOCK_KEY = "tdmm.layoutLocked.v1";

function load(): boolean {
  try {
    return localStorage.getItem(LAYOUT_LOCK_KEY) !== "false";
  } catch {
    return true;
  }
}

let locked = load();
const listeners = new Set<() => void>();

export function getLayoutLocked(): boolean {
  return locked;
}

export function setLayoutLocked(next: boolean): void {
  if (next === locked) return;
  locked = next;
  try {
    localStorage.setItem(LAYOUT_LOCK_KEY, String(next));
  } catch {
    /* private mode / blocked storage: keep the in-memory value */
  }
  listeners.forEach((l) => l());
}

/** Test hook: re-read the persisted value. */
export function reloadLayoutLock(): void {
  locked = load();
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => void listeners.delete(l);
}

export function useLayoutLocked(): boolean {
  return useSyncExternalStore(subscribe, getLayoutLocked, () => true);
}
