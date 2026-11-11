/**
 * Global "layout locked" flag shared by the docks (model detail, print
 * dashboard) and the stats grid. Locked by default; unlocking is an explicit
 * "Rearrange" action and is never persisted: every page load starts locked.
 */
import { useSyncExternalStore } from "react";

let locked = true;
const listeners = new Set<() => void>();

export function getLayoutLocked(): boolean {
  return locked;
}

export function setLayoutLocked(next: boolean): void {
  if (next === locked) return;
  locked = next;
  listeners.forEach((l) => l());
}

/** Test hook: back to the default (locked). */
export function reloadLayoutLock(): void {
  setLayoutLocked(true);
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => void listeners.delete(l);
}

export function useLayoutLocked(): boolean {
  return useSyncExternalStore(subscribe, getLayoutLocked, () => true);
}
