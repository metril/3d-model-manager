/**
 * T5 spool housekeeping hooks: suppliers, storage locations (+ HA sensors),
 * the colour catalog, spool bulk actions, CSV import/export and labels.
 * Mutations that change spool rows invalidate `["spools"]`.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/api/client";
import type {
  BulkResult,
  BulkSpoolUpdate,
  ColorCatalogEntry,
  ColorCatalogIn,
  CsvImportPreview,
  CsvImportResult,
  HaEntity,
  LabelFormat,
  LabelRequest,
  LocationSensor,
  LocationSensorIn,
  SensorReading,
  SpoolSupplierLink,
  StorageLocation,
  StorageLocationIn,
  Supplier,
  SupplierIn,
} from "@/api/types";

const API_BASE = "/api";

// ---- binary helpers ----

async function failFrom(response: Response): Promise<never> {
  let detail = response.statusText || `Request failed with status ${response.status}`;
  try {
    const data: unknown = await response.json();
    if (data && typeof data === "object" && typeof (data as { detail?: unknown }).detail === "string") {
      detail = (data as { detail: string }).detail;
    }
  } catch {
    // not JSON
  }
  throw new ApiError(response.status, detail);
}

export async function fetchBlob(path: string, init?: { method?: string; body?: unknown }): Promise<Blob> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: init?.method ?? "GET",
    credentials: "include",
    headers: init?.body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  if (!response.ok) await failFrom(response);
  return response.blob();
}

/** Save a blob through a transient anchor. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---- suppliers ----

export function useSuppliers() {
  return useQuery({ queryKey: ["suppliers"], queryFn: () => api.get<Supplier[]>("/suppliers") });
}
export function useCreateSupplier() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: SupplierIn) => api.post<Supplier>("/suppliers", p),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["suppliers"] }),
  });
}
export function usePatchSupplier() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Partial<SupplierIn> }) =>
      api.patch<Supplier>(`/suppliers/${id}`, payload),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["suppliers"] }),
  });
}
export function useDeleteSupplier() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/suppliers/${id}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["suppliers"] });
      void qc.invalidateQueries({ queryKey: ["spools"] });
    },
  });
}

// ---- storage locations + sensors ----

export function useLocations() {
  return useQuery({ queryKey: ["locations"], queryFn: () => api.get<StorageLocation[]>("/storage-locations") });
}
export function useCreateLocation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: StorageLocationIn) => api.post<StorageLocation>("/storage-locations", p),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["locations"] }),
  });
}
export function usePatchLocation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Partial<StorageLocationIn> }) =>
      api.patch<StorageLocation>(`/storage-locations/${id}`, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["locations"] });
      void qc.invalidateQueries({ queryKey: ["spools"] });
    },
  });
}
export function useDeleteLocation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/storage-locations/${id}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["locations"] });
      void qc.invalidateQueries({ queryKey: ["spools"] });
    },
  });
}

export function useLocationSensors(locationId: number | null) {
  return useQuery({
    queryKey: ["locations", locationId, "sensors"],
    queryFn: () => api.get<LocationSensor[]>(`/storage-locations/${locationId}/sensors`),
    enabled: locationId != null,
  });
}
export function useLocationReadings(locationId: number | null) {
  return useQuery({
    queryKey: ["locations", locationId, "readings"],
    queryFn: () => api.get<SensorReading[]>(`/storage-locations/${locationId}/readings`),
    enabled: locationId != null,
    refetchInterval: 30_000,
  });
}
export function useHaEntities(enabled = true) {
  return useQuery({
    queryKey: ["locations", "ha-entities"],
    queryFn: () => api.get<HaEntity[]>("/storage-locations/sensors/entities"),
    enabled,
  });
}
export function useCreateSensor(locationId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: LocationSensorIn) => api.post<LocationSensor>(`/storage-locations/${locationId}/sensors`, p),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["locations"] }),
  });
}
export function usePatchSensor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Partial<LocationSensorIn> }) =>
      api.patch<LocationSensor>(`/storage-locations/sensors/${id}`, payload),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["locations"] }),
  });
}
export function useDeleteSensor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/storage-locations/sensors/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["locations"] }),
  });
}

// ---- colour catalog ----

export function useColorCatalog() {
  return useQuery({ queryKey: ["color-catalog"], queryFn: () => api.get<ColorCatalogEntry[]>("/color-catalog") });
}
export function useColorSearch(q: string) {
  const term = q.trim();
  return useQuery({
    queryKey: ["color-catalog", "search", term],
    queryFn: () => api.get<ColorCatalogEntry[]>(`/color-catalog/search?q=${encodeURIComponent(term)}`),
    enabled: term.length >= 2,
  });
}
export function lookupColor(manufacturer: string, name: string) {
  return api.get<ColorCatalogEntry | null>(
    `/color-catalog/lookup?manufacturer=${encodeURIComponent(manufacturer)}&name=${encodeURIComponent(name)}`,
  );
}
export function useColorsByMaterial(material: string | null) {
  return useQuery({
    queryKey: ["color-catalog", "by-material", material],
    queryFn: () => api.get<ColorCatalogEntry[]>(`/color-catalog/by-material/${encodeURIComponent(material ?? "")}`),
    enabled: !!material,
  });
}
export function useCreateColor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: ColorCatalogIn) => api.post<ColorCatalogEntry>("/color-catalog", p),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["color-catalog"] }),
  });
}
export function usePatchColor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Partial<ColorCatalogIn> }) =>
      api.patch<ColorCatalogEntry>(`/color-catalog/${id}`, payload),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["color-catalog"] }),
  });
}
export function useDeleteColor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/color-catalog/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["color-catalog"] }),
  });
}
export function useResetColorCatalog() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<void>("/color-catalog/reset"),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["color-catalog"] }),
  });
}

// ---- spool suppliers + bulk ----

export function usePutSpoolSuppliers() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, links }: { id: number; links: SpoolSupplierLink[] }) =>
      api.put<SpoolSupplierLink[]>(`/spools/${id}/suppliers`, links),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["spools"] }),
  });
}

export function useBulkUpdateSpools() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ ids, update }: { ids: number[]; update: BulkSpoolUpdate }) =>
      api.post<BulkResult>("/spools/bulk-update", { ids, update }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["spools"] }),
  });
}

export type BulkAction = "delete" | "archive" | "restore";
export function useBulkSpoolAction(action: BulkAction) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ids: number[]) => api.post<unknown>(`/spools/bulk-${action}`, { ids }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["spools"] }),
  });
}

// ---- CSV ----

export function useExportCsv() {
  return useMutation({
    mutationFn: async () => downloadBlob(await fetchBlob("/spools/export.csv"), "spools.csv"),
  });
}
export function useImportDryRun() {
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api.postForm<CsvImportPreview>("/spools/import?dry_run=true", form);
    },
  });
}
export function useImportCommit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (token: string) => api.post<CsvImportResult>("/spools/import/commit", { token }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["spools"] }),
  });
}

// ---- labels ----

export function useLabelPreview(req: LabelRequest | null) {
  return useQuery({
    queryKey: ["spool-labels", "preview", req],
    queryFn: async () => URL.createObjectURL(await fetchBlob("/spools/labels/preview", { method: "POST", body: req })),
    enabled: req != null && req.ids.length > 0,
    staleTime: 60_000,
    gcTime: 0,
  });
}
export function useDownloadLabels() {
  return useMutation({
    mutationFn: async ({ req, format }: { req: LabelRequest; format: LabelFormat }) =>
      downloadBlob(await fetchBlob("/spools/labels", { method: "POST", body: { ...req, format } }), `labels.${format}`),
  });
}

