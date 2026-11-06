/**
 * Feature-flag probe (M4 Task 8). Session-gated but NOT printer-gated on the
 * backend (`backend/app/api/features.py`) -- the frontend reads it to decide
 * whether to show the Printer nav/route and per-file Print buttons, so it
 * must answer even when the printer feature is off.
 */
import { queryOptions, useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { Features } from "@/api/types";
import { setCurrency } from "@/lib/format";

export const featuresQueryOptions = queryOptions({
  queryKey: ["features"] as const,
  queryFn: async () => {
    const features = await api.get<Features>("/features");
    setCurrency(features.currency);
    return features;
  },
  staleTime: Infinity, // a process-level flag; no need to refetch
});

export function useFeatures() {
  return useQuery(featuresQueryOptions);
}
