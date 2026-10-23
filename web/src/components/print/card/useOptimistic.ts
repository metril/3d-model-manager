import { useEffect, useState } from "react";

export const REVERT_MS = 10_000;

/** Optimistic value: shown until the reported value matches or 10 s pass. */
export function useOptimistic(reported: number | null | undefined) {
  const [pending, setPending] = useState<number | null>(null);
  useEffect(() => {
    if (pending == null) return;
    if (reported != null && Math.round(reported) === pending) {
      setPending(null);
      return;
    }
    const t = setTimeout(() => setPending(null), REVERT_MS);
    return () => clearTimeout(t);
  }, [pending, reported]);
  return [pending ?? reported ?? null, setPending, pending != null] as const;
}
