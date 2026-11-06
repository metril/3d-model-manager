import { useEffect, useRef, useState } from "react";

/** `[mounted, session]`: `mounted` is true while `open`, and for `closeMs`
 * after it turns false so the close animation can finish. `session` bumps on
 * every closed -> open transition; use it as a React `key` so a reopen inside
 * the close window still starts from fresh state. Dialogs mount their
 * (query-heavy) body only while mounted, so closed per-card instances cost
 * nothing. */
export function useMountedWhileOpen(open: boolean, closeMs = 200): readonly [boolean, number] {
  const [mounted, setMounted] = useState(open);
  const session = useRef(open ? 1 : 0);
  const wasOpen = useRef(open);
  if (open && !wasOpen.current) session.current += 1;
  wasOpen.current = open;
  useEffect(() => {
    if (open) {
      setMounted(true);
      return;
    }
    const id = setTimeout(() => setMounted(false), closeMs);
    return () => clearTimeout(id);
  }, [open, closeMs]);
  return [open || mounted, session.current] as const;
}
