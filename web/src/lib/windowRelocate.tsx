import { createContext, useContext, useEffect, useRef, type ReactNode } from "react";

/** Tiny emitter announcing "a hosted surface may have moved to another
 * browser window" (dock pop-out / re-dock). Surfaces that schedule work on a
 * specific window's `requestAnimationFrame` (the 3D viewer) subscribe so they
 * can re-target. Outside a dock there is no provider and the hook is a no-op. */
export type RelocateEmitter = {
  emit: () => void;
  subscribe: (listener: () => void) => () => void;
};

export function createRelocateEmitter(): RelocateEmitter {
  const listeners = new Set<() => void>();
  return {
    emit: () => listeners.forEach((listener) => listener()),
    subscribe: (listener) => {
      listeners.add(listener);
      return () => void listeners.delete(listener);
    },
  };
}

const RelocateContext = createContext<RelocateEmitter | null>(null);

export function RelocateProvider({ emitter, children }: { emitter: RelocateEmitter; children: ReactNode }) {
  return <RelocateContext.Provider value={emitter}>{children}</RelocateContext.Provider>;
}

export function useRelocateEmitter(): RelocateEmitter | null {
  return useContext(RelocateContext);
}

/** Calls `callback` whenever the surrounding dock moves a panel between windows. */
export function useOnRelocate(callback: () => void): void {
  const emitter = useContext(RelocateContext);
  const ref = useRef(callback);
  useEffect(() => {
    ref.current = callback;
  });
  useEffect(() => emitter?.subscribe(() => ref.current()), [emitter]);
}
