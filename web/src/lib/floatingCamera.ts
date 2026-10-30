/**
 * Floating camera overlay store: which printer is shown, whether it is
 * minimised, and its rect. Persisted to localStorage. A module-level store
 * (not React state) so the overlay survives route changes and CameraTile
 * can yield its stream to the overlay (one connection per printer).
 */
import { useSyncExternalStore } from "react";

export interface FloatingRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface FloatingCameraState {
  printerId: number | null;
  minimized: boolean;
  rect: FloatingRect;
}

export const MIN_W = 200;
export const MIN_H = 120;
const KEY = "tdmm.floatingCamera";
const DEFAULT_RECT: FloatingRect = { x: 24, y: 80, w: 360, h: 240 };

function clampRect(r: FloatingRect): FloatingRect {
  const vw = typeof window === "undefined" ? 1920 : window.innerWidth;
  const vh = typeof window === "undefined" ? 1080 : window.innerHeight;
  const w = Math.min(Math.max(MIN_W, r.w), Math.max(MIN_W, vw));
  const h = Math.min(Math.max(MIN_H, r.h), Math.max(MIN_H, vh));
  return {
    w,
    h,
    x: Math.min(Math.max(0, r.x), Math.max(0, vw - w)),
    y: Math.min(Math.max(0, r.y), Math.max(0, vh - h)),
  };
}

function load(): FloatingCameraState {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "null") as Partial<FloatingCameraState> | null;
    if (raw && typeof raw === "object") {
      const r = raw.rect;
      const ok = r && [r.x, r.y, r.w, r.h].every((n) => typeof n === "number" && Number.isFinite(n));
      return {
        printerId: typeof raw.printerId === "number" ? raw.printerId : null,
        minimized: raw.minimized === true,
        rect: ok ? clampRect(r as FloatingRect) : DEFAULT_RECT,
      };
    }
  } catch {
    /* unavailable or corrupt storage: defaults */
  }
  return { printerId: null, minimized: false, rect: DEFAULT_RECT };
}

let state: FloatingCameraState = load();
const listeners = new Set<() => void>();

function set(next: FloatingCameraState) {
  state = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l());
}

export const floatingCamera = {
  get: () => state,
  subscribe(l: () => void) {
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  },
  open(printerId: number) {
    set({ ...state, printerId, minimized: false, rect: clampRect(state.rect) });
  },
  /** Open when closed / on another printer, close when already showing it. */
  toggle(printerId: number) {
    if (state.printerId === printerId) floatingCamera.close();
    else floatingCamera.open(printerId);
  },
  close() {
    set({ ...state, printerId: null, minimized: false });
  },
  setMinimized(minimized: boolean) {
    set({ ...state, minimized });
  },
  setRect(rect: FloatingRect) {
    set({ ...state, rect: clampRect(rect) });
  },
  /** Test hook: re-read storage. */
  reload() {
    state = load();
    listeners.forEach((l) => l());
  },
};

export function useFloatingCamera(): FloatingCameraState {
  return useSyncExternalStore(floatingCamera.subscribe, floatingCamera.get, floatingCamera.get);
}

/** True while the overlay is actively streaming this printer (CameraTile
 * then drops its own connection). */
export function useOverlayOwnsStream(printerId: number): boolean {
  const s = useFloatingCamera();
  return s.printerId === printerId && !s.minimized;
}
