import { useCallback, useEffect, useState, type RefObject } from "react";

/** Fullscreen API wrapper for one element: `{ active, supported, toggle }`.
 * `active` tracks `fullscreenchange` so Esc-exit stays in sync. */
export function useElementFullscreen(target: RefObject<HTMLElement | null>) {
  const [active, setActive] = useState(false);
  const supported = typeof document !== "undefined" && typeof document.documentElement.requestFullscreen === "function";

  useEffect(() => {
    const onChange = () => setActive(document.fullscreenElement != null && document.fullscreenElement === target.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, [target]);

  const toggle = useCallback(() => {
    if (document.fullscreenElement) {
      void document.exitFullscreen?.();
    } else {
      void target.current?.requestFullscreen?.();
    }
  }, [target]);

  return { active, supported, toggle };
}
