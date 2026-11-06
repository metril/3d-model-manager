import { useEffect, useState } from "react";

export const REVERT_MS = 10_000;

/** Optimistic value: shown until the reported value matches or 10 s pass. */
export function useOptimistic(reported: number | null | undefined) {
  const [pending, setPending] = useState<number | null>(null);
  useEffect(() => {
    if (pending != null && reported != null && Math.round(reported) === pending) setPending(null);
  }, [pending, reported]);
  // Keyed on `pending` alone: status polls changing `reported` must not
  // keep restarting the revert timer (it would then never fire).
  useEffect(() => {
    if (pending == null) return;
    const t = setTimeout(() => setPending(null), REVERT_MS);
    return () => clearTimeout(t);
  }, [pending]);
  return [pending ?? reported ?? null, setPending, pending != null] as const;
}
