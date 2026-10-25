/**
 * Tiny module-level store of the slice jobs the user started, so a progress
 * toast keeps following a job after the dialog or page that started it is
 * gone (`SliceJobsHost`, mounted once in the app shell, renders the watchers).
 */
import { useSyncExternalStore } from "react";

export interface TrackedSlice {
  id: string;
  fileName: string;
}

let tracked: TrackedSlice[] = [];
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

export function trackSlice(item: TrackedSlice) {
  if (tracked.some((t) => t.id === item.id)) return;
  tracked = [...tracked, item];
  emit();
}

export function untrackSlice(id: string) {
  if (!tracked.some((t) => t.id === id)) return;
  tracked = tracked.filter((t) => t.id !== id);
  emit();
}

export function useTrackedSlices(): TrackedSlice[] {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => tracked,
  );
}
