/** T2 K-profile (pressure advance) hooks: printer profile list/CRUD/select/notes and per-spool links. */
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { toastControlError } from "@/api/printerControl";
import type {
  KProfile,
  KProfileIn,
  KProfileList,
  KProfilePending,
  KProfileSelectIn,
  SpoolKProfile,
} from "@/api/types";

/** Poll cadence for a 202-pending list; mutable so tests can shrink it. */
export const kprofileTiming = { pollMs: 1000, maxMs: 15_000 };

export interface KProfileScope {
  nozzle: string;
  /** Dual-nozzle only; omit for all extruders. */
  extruder?: number;
}

const base = (printerId: number) => `/printers/${printerId}/kprofiles`;
const listKey = (printerId: number, s?: KProfileScope) =>
  ["printers", printerId, "kprofiles", s?.nozzle ?? null, s?.extruder ?? null] as const;
const allKey = (printerId: number) => ["printers", printerId, "kprofiles"] as const;

function isPending(r: KProfileList | KProfilePending): r is KProfilePending {
  return (r as KProfilePending).status === "pending" && !("profiles" in r);
}

function qs(s: KProfileScope, extra: Record<string, string> = {}): string {
  const p = new URLSearchParams({ nozzle: s.nozzle, ...extra });
  if (s.extruder != null) p.set("extruder", String(s.extruder));
  return `?${p.toString()}`;
}

export async function fetchKProfiles(printerId: number, scope: KProfileScope, refresh = false): Promise<KProfileList> {
  let res = await api.get<KProfileList | KProfilePending>(`${base(printerId)}${qs(scope, refresh ? { refresh: "1" } : {})}`);
  const start = Date.now();
  while (isPending(res)) {
    if (Date.now() - start >= kprofileTiming.maxMs) throw new Error("K-profile request timed out");
    await new Promise((r) => setTimeout(r, kprofileTiming.pollMs));
    res = await api.get<KProfileList | KProfilePending>(
      `${base(printerId)}${qs(scope, { request_id: res.request_id })}`,
    );
  }
  return res;
}

export function useKProfiles(printerId: number, scope: KProfileScope, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: listKey(printerId, scope),
    queryFn: () => fetchKProfiles(printerId, scope),
    enabled: options?.enabled ?? true,
    staleTime: 30_000,
    retry: false,
  });
}

/** Force a printer round trip and replace the cached list. */
export function useRefreshKProfiles(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (scope: KProfileScope) => fetchKProfiles(printerId, scope, true),
    onSuccess: (data, scope) => qc.setQueryData(listKey(printerId, scope), data),
    onError: toastControlError,
  });
}

function invalidateList(qc: QueryClient, printerId: number) {
  void qc.invalidateQueries({ queryKey: allKey(printerId) });
}

export function useSaveKProfile(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: KProfileIn) => api.post<{ ok: boolean; payload?: { setting_id?: string } }>(base(printerId), b),
    onSuccess: () => invalidateList(qc, printerId),
    onError: toastControlError,
  });
}

export function useDeleteKProfile(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ profile, extruder }: { profile: KProfile; extruder?: number }) => {
      const p = new URLSearchParams({ nozzle: String(profile.nozzle_diameter), cali_idx: String(profile.cali_idx) });
      if (extruder != null) p.set("extruder", String(extruder));
      return api.delete<{ ok: boolean }>(`${base(printerId)}/${encodeURIComponent(profile.setting_id)}?${p.toString()}`);
    },
    onSuccess: () => invalidateList(qc, printerId),
    onError: toastControlError,
  });
}

export function useSelectKProfile(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: KProfileSelectIn) => api.post<{ ok: boolean }>(`${base(printerId)}/select`, b),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["printers", printerId, "status"] });
      void qc.invalidateQueries({ queryKey: ["printers", "overview"] });
    },
    onError: toastControlError,
  });
}

/** Empty/blank note deletes it. */
export function useKProfileNote(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ settingId, note }: { settingId: string; note: string }) => {
      const url = `${base(printerId)}/${encodeURIComponent(settingId)}/note`;
      return note.trim() ? api.put<{ setting_id: string; note: string }>(url, { note: note.trim() }) : api.delete<void>(url);
    },
    onSuccess: () => invalidateList(qc, printerId),
    onError: toastControlError,
  });
}

const spoolKey = (spoolId: number) => ["spools", spoolId, "kprofiles"] as const;

export function useSpoolKProfiles(spoolId: number | null | undefined) {
  return useQuery({
    queryKey: spoolKey(spoolId ?? 0),
    queryFn: () => api.get<SpoolKProfile[]>(`/spools/${spoolId}/kprofiles`),
    enabled: spoolId != null,
  });
}

/** Replaces the spool's whole link list. */
export function usePutSpoolKProfiles(spoolId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (links: SpoolKProfile[]) => api.put<SpoolKProfile[]>(`/spools/${spoolId}/kprofiles`, links),
    onSuccess: (data) => qc.setQueryData(spoolKey(spoolId), data),
    onError: toastControlError,
  });
}

export function useDeleteSpoolKProfile(spoolId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (l: Pick<SpoolKProfile, "printer_id" | "extruder" | "nozzle_diameter">) =>
      api.delete<void>(`/spools/${spoolId}/kprofiles/${l.printer_id}?extruder=${l.extruder}&nozzle=${l.nozzle_diameter}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: spoolKey(spoolId) }),
    onError: toastControlError,
  });
}

/** Merge one link into a spool's list, replacing the same printer/extruder/nozzle. */
export function mergeSpoolLink(existing: SpoolKProfile[], link: SpoolKProfile): SpoolKProfile[] {
  return [
    ...existing.filter(
      (l) => !(l.printer_id === link.printer_id && l.extruder === link.extruder && l.nozzle_diameter === link.nozzle_diameter),
    ),
    link,
  ];
}

export function profileToLink(printerId: number, p: KProfile): SpoolKProfile {
  return {
    printer_id: printerId,
    extruder: p.extruder_id,
    nozzle_diameter: String(p.nozzle_diameter),
    nozzle_type: null,
    k_value: p.k_value,
    n_coef: p.n_coef,
    name: p.name,
    cali_idx: p.cali_idx,
    setting_id: p.setting_id,
  };
}
