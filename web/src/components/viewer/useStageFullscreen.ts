import { useCallback, useEffect, useLayoutEffect, useState, type RefObject } from "react";

import { useHotkeys } from "@/hooks/useHotkeys";
import { useOnRelocate } from "@/lib/windowRelocate";

/** Fullscreen toggle for the viewer stage, bound to whichever document hosts
 * it (the main window, or a dock pop-out): `Shift+F`, the change listener and
 * `exitFullscreen` all use the stage's `ownerDocument`, re-resolved when the
 * dock moves the stage between windows. */
export function useStageFullscreen(stageRef: RefObject<HTMLElement | null>, active: boolean) {
  const [hostDoc, setHostDoc] = useState<Document>(document);
  const sync = useCallback(() => setHostDoc(stageRef.current?.ownerDocument ?? document), [stageRef]);
  useLayoutEffect(sync, [sync]);
  useOnRelocate(sync);

  const toggleFullscreen = useCallback(() => {
    const doc = stageRef.current?.ownerDocument ?? document;
    if (doc.fullscreenElement) void doc.exitFullscreen();
    else void stageRef.current?.requestFullscreen();
  }, [stageRef]);
  useHotkeys({ F: toggleFullscreen }, { enabled: active, doc: hostDoc });

  // The Fullscreen API's change event is the only source of truth (the request
  // is async, and ESC/browser chrome can exit without going through the toggle).
  const [isFullscreen, setIsFullscreen] = useState(false);
  useEffect(() => {
    const handleChange = () => setIsFullscreen(hostDoc.fullscreenElement === stageRef.current);
    hostDoc.addEventListener("fullscreenchange", handleChange);
    return () => hostDoc.removeEventListener("fullscreenchange", handleChange);
  }, [hostDoc, stageRef]);

  return { isFullscreen, toggleFullscreen };
}
