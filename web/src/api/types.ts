/**
 * Payload types for the `/api` backend, hand-mirrored from
 * `backend/app/schemas/*.py` (source of truth — keep in sync by hand, no
 * codegen per Task 8 decisions).
 */

// -- enums (backend/app/models/enums.py) -----------------------------------

export const BLOB_FORMATS = [
  "stl",
  "3mf",
  "obj",
  "step",
  "iges",
  "gcode_3mf",
  "gcode",
  "png",
  "jpg",
  "webp",
  // R13c: doc file kinds (`BlobKind.doc`).
  "pdf",
  "md",
  "txt",
  "docx",
  // P4 camera plan: timelapse video formats (`BlobKind.video`).
  "mp4",
  "avi",
  "other",
] as const;

export type BlobFormat = (typeof BLOB_FORMATS)[number];

export type BlobKind =
  | "mesh"
  | "cad"
  | "sliced"
  | "gcode"
  | "image"
  | "doc"
  | "video"
  | "other";

// -- models (backend/app/schemas/library.py) -------------------------------

export interface ModelCreate {
  name: string;
  description?: string | null;
}

export interface ModelPatch {
  name?: string;
  description?: string | null;
  cover_blob_hash?: string | null;
  review_state?: string | null;
  favorite?: boolean;
  // feat/import-fidelity T3: the soft-delete toggle -- `DELETE
  // /models/{slug}` is now a real hard delete, so archiving/unarchiving a
  // model is reversible PATCH traffic instead (both directions).
  is_archived?: boolean;
  // R13b: single category assignment -- `null` clears it.
  category_id?: number | null;
  // R13c: freeform key/value custom fields (`MetadataEditor`) and a
  // freeform print-tips note -- both `null` clear the field entirely.
  metadata?: Record<string, string> | null;
  print_tips?: string | null;
}

// `POST /models/{slug}/redownload` payload (feat/import-fidelity T3):
// re-fetches this model's files fresh from its original import source.
// `mode: "revision"` lands the fresh download as a new revision (old
// revision untouched); `mode: "replace"` overwrites the current revision's
// files in place. 409s when the model has no resolvable import source.
export interface ModelRedownloadIn {
  mode: "revision" | "replace";
}

// `POST /models/bulk` (Branch 4 Task 1): apply the same tag/favorite changes
// to every model in `ids` in one call.
export interface ModelBulkIn {
  ids: number[];
  add_tags?: string[];
  remove_tags?: string[];
  favorite?: boolean;
}

export interface ModelBulkOut {
  updated: number;
}

// `POST /models/bulk-delete` (Round 11 T1/T3): hard-deletes every model in
// `ids` in one call -- same route-ordering rationale as `/bulk` above.
export interface ModelBulkDeleteOut {
  deleted: number;
}

// Multi-backend storage (backend/app/schemas/library.py, Workstream C task
// C3/C4) -- `POST /models/{slug}/relocate` moves or replicates every file of
// a model onto another configured backend.
export interface ModelRelocateIn {
  target_backend_id: number;
  mode: "move" | "replicate";
}

// The DISTINCT storage backends holding a model's current-revision files
// (`files.backend_id`, PRIMARY location only -- see
// `app.services.library._model_backends_summary`).
export interface ModelBackendOut {
  id: number;
  name: string;
}

export interface ModelSummary {
  id: number;
  slug: string;
  name: string;
  description: string | null;
  tags: string[];
  updated_at: string;
  created_at: string;
  file_count: number;
  formats: BlobFormat[];
  cover: string | null;
  // T2: the revision's own assembly-thumbnail render URL specifically
  // (`null` until that derivative is OK) -- independent of `cover` above,
  // which may show a site/user cover image instead even once the render is
  // also ready.
  render_url: string | null;
  print_time_s: number | null;
  has_sliced: boolean;
  source_site: string | null;
  review_state?: string | null;
  source_collection_id: number | null;
  source_collection_title: string | null;
  favorite: boolean;
  // Phase 6: card-level dims + file picks, computed per current revision
  // (backend/app/services/library.py `_gallery_aggregates`).
  dims_mm: number[] | null;
  best_slicer_file: FileOut | null;
  printable_file: FileOut | null;
  // R13b (categories): a model's single category, or `null` for uncategorized.
  // `category_id` mirrors it for cache-patching convenience; both come from
  // the same backend join and always agree.
  category_id?: number | null;
  category?: CategoryOut | null;
}

export interface GalleryPage {
  items: ModelSummary[];
  next_cursor: string | null;
}

// -- blob metadata / plates (backend/app/schemas/library.py, Task 7) -------

export interface PlateFilamentOut {
  type: string | null;
  color: string | null;
  used_m: number | null;
  used_g: number | null;
}

export interface PlateOut {
  index: number;
  prediction_s: number | null;
  weight_g: number | null;
  est_cost: number | null;
  thumbnail_available: boolean;
  filaments: PlateFilamentOut[];
}

export interface BlobMetaOut {
  triangle_count: number | null;
  dims_mm: number[] | null;
  volume_cm3: number | null;
  surface_area_cm2: number | null;
  is_watertight: boolean | null;
  print_time_s: number | null;
  filament_g: number | null;
  filament_m: number | null;
  filament_types: string[] | null;
  layer_height: number | null;
  nozzle: number | null;
  printer_model: string | null;
  plate_count: number | null;
  layer_count: number | null;
  infill_pct: number | null;
  slicer: string | null;
  plates: PlateOut[] | null;
}

// `null` means the blob's format never produces a GLB at all, distinct from
// a GLB-format blob that simply hasn't been converted yet ("pending").
export type GlbStatus = "ok" | "pending" | "failed" | "unsupported";

// -- files / notes ----------------------------------------------------------

export interface FileOut {
  id: number;
  revision_id: number;
  rel_path: string;
  storage_path: string;
  blob_hash: string;
  size: number;
  format: BlobFormat;
  kind: BlobKind;
  mtime: string | null;
  verified_at: string | null;
  meta: BlobMetaOut | null;
  thumb_ready: boolean;
  glb_status: GlbStatus | null;
  glb_preview_ready: boolean;
}

export interface NoteCreate {
  model_id: number;
  revision_id?: number | null;
  body: string;
}

export interface NotePatch {
  body: string;
}

export interface NoteOut {
  id: number;
  model_id: number;
  revision_id: number | null;
  body: string;
  created_at: string;
  updated_at: string;
}

// -- revisions ----------------------------------------------------------

export interface RevisionCreate {
  name?: string | null;
  note?: string | null;
}

export interface RevisionSummary {
  id: number;
  model_id: number;
  number: number;
  name: string | null;
  note: string | null;
  dir_name: string;
  created_at: string;
  file_count: number;
}

export interface RevisionDetail {
  id: number;
  model_id: number;
  number: number;
  name: string | null;
  note: string | null;
  dir_name: string;
  created_at: string;
  files: FileOut[];
  notes: NoteOut[];
}

export interface ModelDetail {
  id: number;
  created_by_user_id?: number | null;
  created_by_username?: string | null;
  slug: string;
  name: string;
  description: string | null;
  source_url: string | null;
  source_site: string | null;
  source_author: string | null;
  source_license: string | null;
  source_collection_id: number | null;
  source_collection_title: string | null;
  imported_at: string | null;
  cover_blob_hash: string | null;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
  tags: string[];
  current_revision: RevisionDetail | null;
  notes: NoteOut[];
  review_state?: string | null;
  backends: ModelBackendOut[];
  favorite: boolean;
  // Branch 5 Task 1 print-history aggregates -- zero-state is
  // `print_count: 0, last_printed_at: null` for a model with no logged prints.
  print_count: number;
  last_printed_at: string | null;
  // R13b (categories): mirrors `ModelSummary.category`/`category_id` above.
  category_id?: number | null;
  category?: CategoryOut | null;
  // R13c: freeform key/value custom fields + a freeform print-tips note.
  metadata: Record<string, string> | null;
  print_tips: string | null;
}

// -- diff ----------------------------------------------------------

export interface DiffEntrySide {
  blob_hash: string;
  size: number;
}

export interface DiffEntry {
  rel_path: string;
  a: DiffEntrySide | null;
  b: DiffEntrySide | null;
}

export interface DiffResponse {
  added: DiffEntry[];
  removed: DiffEntry[];
  changed: DiffEntry[];
  unchanged: DiffEntry[];
}

// -- tags ----------------------------------------------------------

export type TagColor =
  | "slate"
  | "red"
  | "orange"
  | "amber"
  | "green"
  | "teal"
  | "blue"
  | "indigo"
  | "violet"
  | "pink";

export interface TagCreate {
  name: string;
  color?: TagColor | null;
}

export interface TagOut {
  id: number;
  name: string;
  color?: TagColor | null;
}

// -- categories (R13b, backend/app/schemas/categories.py) ------------------
// Unlike tags (many-per-model), a model has AT MOST ONE category -- a
// coarser, exclusive grouping (mirrors GyroidVault's shelves). `color` is a
// `TagColor` palette name, same fixed 10-key palette tags use -- rendered
// through `tagColorClass`/the same swatch picker pattern as `TagEditor`, not
// a free-form hex string.

export interface CategoryOut {
  id: number;
  name: string;
  color: TagColor | null;
  model_count: number;
}

export interface CategoryCreate {
  name: string;
  color?: TagColor | null;
}

export interface CategoryPatch {
  name?: string;
  color?: TagColor | null;
}

// -- uploads (backend/app/schemas/uploads.py) --------------------------------

export interface UploadResult {
  file_id: number;
  blob_hash: string;
  size: number;
  job_id: string;
}

export interface ExistingUploadModel {
  slug: string;
  name: string;
  url: string;
}

/** ``PUT /uploads`` 409 body: this content already exists elsewhere in the
 * library (R11-C item 18). */
export interface DuplicateUploadOut {
  detail: "duplicate";
  existing: ExistingUploadModel;
  suggested_name: string;
}

// -- jobs (backend/app/schemas/jobs.py) --------------------------------

// `dead` (Task 8): a formal dead-letter state, auto-parked once a job has
// exhausted its `max_attempts` ceiling -- distinct from a plain `failed`
// that's still worth retrying automatically.
export type JobState = "queued" | "running" | "done" | "failed" | "dead";

export interface JobOut {
  id: string;
  celery_id: string | null;
  type: string;
  subject_type: string | null;
  subject_id: number | null;
  state: JobState;
  attempts: number;
  max_attempts: number;
  error: string | null;
  created_at: string;
  updated_at: string;
}

// -- settings / storage config (backend/app/schemas/settings.py, Task 7) --

export type StorageScheme = "local" | "smb" | "s3";

export interface StorageConfigOut {
  backend: StorageScheme;
  config: Record<string, unknown>;
}

export interface StorageConfigIn {
  backend: StorageScheme;
  config: Record<string, unknown>;
}

export interface ConnectionTestOut {
  ok: boolean;
  detail: string;
  latency_ms: number;
}

// -- multi-backend storage CRUD (backend/app/schemas/settings.py, Workstream
// C task C3) -- unlike `StorageConfigIn`/`StorageConfigOut` above (the
// legacy single-backend shim), these operate on a specific
// `storage_backends` row; `config` carries its own `backend` discriminator
// field inside it (same shape `StorageConfigOut.config` already uses since
// both come from the same `redacted()` call server-side).

export interface StorageBackendOut {
  id: number;
  name: string;
  scheme: StorageScheme;
  is_default: boolean;
  config: Record<string, unknown>;
  created_at: string;
}

export interface StorageBackendCreateIn {
  name: string;
  config: Record<string, unknown>;
}

export interface StorageBackendUpdateIn {
  name?: string;
  config?: Record<string, unknown>;
}

// -- scan (backend/app/schemas/scan.py, Task 8) ----------------------------

export type ScanState = "queued" | "running" | "done" | "failed" | "skipped";

export interface ScanAdopted {
  model_id: number;
  slug: string;
  revision_id: number;
  files: string[];
}

export interface ScanRelinked {
  file_id: number;
  from: string;
  to: string;
  hash: string;
}

export interface ScanChanged {
  file_id: number;
  storage_path: string;
  old_hash: string;
  new_hash: string;
}

export interface ScanMissing {
  file_id: number;
  storage_path: string;
  model_slug: string;
}

// Added in Task 5's fix wave alongside `verified` -- both must be mirrored
// here even though the earlier Task 5/6 interface note predates them.
export interface ScanError {
  storage_path: string;
  error: string;
}

export interface ScanReport {
  adopted: ScanAdopted[];
  relinked: ScanRelinked[];
  changed: ScanChanged[];
  missing: ScanMissing[];
  errors: ScanError[];
  verified: number;
}

export interface ScanRunOut {
  id: number;
  created_at: string;
  finished_at: string | null;
  state: ScanState;
  files_seen: number;
  files_hashed: number;
  relinked: number;
  adopted: number;
  missing: number;
  report: ScanReport | null;
}

// -- events (SSE, Global Constraints) --------------------------------

export interface JobUpdatedEvent {
  type: "job.updated";
  job_id: string;
  job_type: string;
  state: JobState;
  subject_type: string | null;
  subject_id: number | null;
}

// -- printers/features/events union (backend/app/schemas/printers.py,
// backend/app/api/features.py; M4 Task 8) ----------------------------------

export interface Features {
  printer_enabled: boolean;
  // Round 8 T6: watched-folder slicer auto-import (`app.tasks.slicer_watch`,
  // Round 8 T5). `watch_dir` is the CONTAINER path (or null when
  // unset) -- Settings shows it alongside a note that it maps to
  // `WATCH_HOST_DIR` on the host. `watch_enabled` mirrors
  // the backend's exact beat-entry condition (dir set AND a positive poll
  // interval), so the frontend never re-derives it.
  watch_dir: string | null;
  watch_enabled: boolean;
  // Always true (the virtual printer ships always-on; kept for compat). Its
  // network config lives in Settings -> Server.
  virtual_printer_enabled?: boolean;
  // Slicer enabled + URL set in Settings -> Server (DB only; live health comes
  // from `/settings/server/status` and `/slicer/health`).
  slicer_configured?: boolean;
}

// `GET`/`PUT /settings/app` (backend/app/schemas/settings.py's
// AppSettingsIn/Out, backend/app/services/app_config.py; Round 10 T1) -- the
// DB-backed runtime config behind `Features` above: the printer toggle and
// the four background-schedule intervals, editable from Settings without an
// env var or restart. All five fields are REQUIRED on `PUT` (full replace,
// nothing secret) -- 0 turns a schedule off.
export interface AppSettings {
  printer_enabled: boolean;
  scan_interval_s: number;
  collection_sync_interval_s: number;
  watch_interval_s: number;
  watch_stable_s: number;
  // R11-B item 14 (print cost estimate): currency-agnostic rates the
  // server prices runs and plate estimates with (PrintEntry.total_cost,
  // PlateOut.est_cost).
  filament_cost_per_kg: number;
  machine_cost_per_hour: number;
  // P8 Task 7: prices a run's logged `energy_kwh` into `energy_cost` --
  // same currency-agnostic convention as the two rates above.
  energy_cost_per_kwh: number;
}

// `GET /api/stats` (backend/app/schemas/stats.py, R11-B item 13): cheap
// dashboard aggregates, cached server-side for 30s -- polling this is fine.
export interface StatsOut {
  models: { total: number; favorites: number; archived: number; drafts: number };
  files: {
    total: number;
    bytes_total: number;
    bytes_by_backend: Record<string, number>;
    by_format: Record<string, number>;
  };
  tags: number;
  collections: number;
  prints: {
    total: number;
    succeeded: number;
    failed: number;
    filament_g_total: number;
    duration_s_total: number;
  };
  recent: { models_added_7d: number; prints_7d: number };
  jobs: { running: number; queued: number; failed_24h: number };
  recent_models: ModelSummary[];
  recent_prints: PrintEntry[];
  material_usage: { material_id: number | null; name: string; grams: number; prints: number }[];
}

// -- filament usage stats (backend/app/schemas/stats.py, P5 Task 8) --------

export interface FilamentPointOut {
  t: string;
  grams: number;
  cost: number;
}

export interface FilamentSeriesOut {
  key: string;
  name: string;
  color_hex: string | null;
  total_g: number;
  total_cost: number;
  points: FilamentPointOut[];
}

export interface FilamentStatsOut {
  by_material: FilamentSeriesOut[];
  by_spool: FilamentSeriesOut[];
}

export type PrinterKind = "bambu_lan";

export interface PrinterOut {
  id: number;
  name: string;
  kind: PrinterKind;
  host: string;
  serial: string;
  model: string | null;
  enabled: boolean;
  options: Record<string, unknown>;
  access_code_set: boolean;
  build_volume_mm: { x: number; y: number; z: number } | null;
  // Q1: model capability table + lifetime print hours (optional: older payloads omit them).
  capabilities?: PrinterCapabilities;
  total_print_hours?: number | null;
  maintenance_mode?: boolean;
}

export interface PrinterCapabilities {
  model_key?: string | null;
  chamber_heater: boolean;
  chamber_temp: boolean;
  airduct: boolean;
  door: boolean;
  dual_nozzle: boolean;
  aux_fan: boolean;
  chamber_fan: boolean;
  camera: string;
  lidar: boolean;
  max_bed?: number | null;
  max_nozzle?: number | null;
  ams_drying: boolean;
  verified: boolean;
  /** T3: xcam modules this model reports (print-option rows). */
  xcam_modules?: string[];
  sd_browser?: boolean;
  sd_print?: boolean;
}

export interface PrinterCreate {
  name: string;
  kind?: PrinterKind;
  host: string;
  serial: string;
  access_code: string;
  model?: string | null;
  enabled?: boolean;
  options?: Record<string, unknown>;
  build_volume_mm?: { x: number; y: number; z: number } | null;
}

export interface PrinterUpdate {
  name?: string;
  host?: string;
  serial?: string;
  access_code?: string;
  model?: string | null;
  enabled?: boolean;
  options?: Record<string, unknown>;
  build_volume_mm?: { x: number; y: number; z: number } | null;
}

export interface ProbeOut {
  ok: boolean;
  detail: string;
  gcode_state: string | null;
}

// `POST /printers/detect-serial` (Round 8 T1): reads the serial straight off
// the printer's TLS certificate -- no DB row or access code involved, so the
// "Detect" button works before a printer is even saved.
export interface DetectSerialIn {
  host: string;
  port?: number;
}

export interface DetectSerialOut {
  serial: string | null;
  detail: string;
}

// One AMS filament slot, loaded or empty (M8 G3 + P5 spool tray plan):
// `color` is `#RRGGBB` (alpha stripped) or null for an empty/unknown slot.
// `slot` is the global tray id (unchanged); `ams_unit`/`tray` are the
// per-unit id/tray-within-unit; `tray_uuid` is the RFID tag read off a
// spool (null if empty/untagged). `spool` is filled in once Task 6 ships.
export interface AmsTray {
  slot: number;
  ams_unit: number | null;
  tray: number | null;
  color: string | null;
  material: string | null;
  tray_type: string | null;
  tray_uuid: string | null;
  remain_pct: number | null;
  spool: SpoolBrief | null;
}

export interface SpoolBrief {
  id: number;
  material_type: string;
  brand: string | null;
  color_hex: string | null;
  color_name: string | null;
  low_stock: boolean;
}

// `/api/spools` (P5 spool tray plan, Task 9): physical spool inventory,
// their current AMS slot (if any) and usage ledger. Mirrors
// `backend/app/schemas/inventory.py` 1:1.
export interface SpoolSlot {
  printer_id: number;
  ams_unit: number;
  slot: number;
}

export interface SpoolOut {
  id: number;
  material_id: number | null;
  material_type: string;
  brand: string | null;
  color_hex: string | null;
  color_name: string | null;
  label_weight_g: number;
  empty_weight_g: number | null;
  weight_used_g: number;
  cost_per_kg: number | null;
  location: string | null;
  location_id?: number | null;
  suppliers?: SpoolSupplierLink[];
  low_stock_g: number;
  tray_uuid: string | null;
  spoolman_id?: number | null;
  archived_at: string | null;
  last_used_at: string | null;
  created_by_user_id: number | null;
  created_at: string;
  remaining_g: number;
  low_stock: boolean;
  slot: SpoolSlot | null;
}

export interface SpoolCreate {
  material_id?: number | null;
  material_type: string;
  brand?: string | null;
  color_hex?: string | null;
  color_name?: string | null;
  label_weight_g?: number;
  empty_weight_g?: number | null;
  weight_used_g?: number;
  cost_per_kg?: number | null;
  location_id?: number | null;
  low_stock_g?: number;
  tray_uuid?: string | null;
}

export interface SpoolUpdate {
  material_id?: number | null;
  material_type?: string;
  brand?: string | null;
  color_hex?: string | null;
  color_name?: string | null;
  label_weight_g?: number;
  empty_weight_g?: number | null;
  weight_used_g?: number;
  cost_per_kg?: number | null;
  location_id?: number | null;
  low_stock_g?: number;
  tray_uuid?: string | null;
  archived?: boolean;
}

export interface SpoolUsageRow {
  id: number;
  print_job_id: number | null;
  archive_id: number | null;
  model_name: string | null;
  grams: number;
  source: string;
  created_at: string;
}

export interface SpoolSeriesPoint {
  t: string;
  remaining_g: number;
}

export interface SpoolDetailOut extends SpoolOut {
  usage: SpoolUsageRow[];
  series: SpoolSeriesPoint[];
}

export interface ManualUsageIn {
  grams: number;
}

export interface SlotAssignIn {
  spool_id: number | null;
}

export interface PrinterStatusOut {
  online: boolean;
  gcode_state: string | null;
  mc_percent: number | null;
  layer_num: number | null;
  total_layer_num: number | null;
  mc_remaining_time: number | null;
  print_error: number | null;
  nozzle_temper: number | null;
  bed_temper: number | null;
  subtask_name: string | null;
  wifi_signal: string | null;
  trays: AmsTray[];
  // Q1 status schema v2 -- all optional (fan values are percent).
  nozzle_target?: number | null;
  bed_target?: number | null;
  chamber_temper?: number | null;
  chamber_target?: number | null;
  fan_part_pct?: number | null;
  fan_aux_pct?: number | null;
  fan_chamber_pct?: number | null;
  light_on?: boolean | null;
  speed_level?: number | null;
  stage?: string | null;
  hms?: Array<{ attr: number; code: number }>;
  sdcard?: boolean | null;
  nozzle_type?: string | null;
  firmware?: { ota: string | null; ams: Array<{ id: number; sw_ver: string | null }> } | null;
  ams_units?: AmsUnit[];
  ext_tray?: AmsTray | null;
  /** Loaded tray (AMS `tray_now`): global slot id, 254 = external spool, 255 = none. */
  tray_now?: number | null;
  /** Enclosure door; only reported for models with a door sensor. */
  door_open?: boolean | null;
  print_thumbnail_key?: string | null;
  ipcam?: boolean | null;
  dev_mode?: boolean | null;
  dev_mode_reason?: string | null;
  last_report_at?: string | null;
  awaiting_plate_clear?: boolean;
  print_options?: PrintOptions | null;
}

export interface AmsUnit {
  id: number;
  label?: string | null;
  humidity: number | null;
  temp: number | null;
  serial: string | null;
  fw: string | null;
}

export interface DevModeWarning {
  printer_id: number;
  name: string;
  reason: string | null;
}

export interface PrintableObject {
  id: number;
  name: string;
  plate: number;
}

export interface ActiveObjectsOut {
  job_id: number | null;
  plate: number | null;
  objects: PrintableObject[];
}

// `POST /printers/{id}/camera/token` (P4 camera plan, Task 4/8): relative
// stream/snapshot URLs carrying a scoped camera token in the query string.
// Cached client-side under its own `["camera-url", id]` key, separate from
// the `["printers"]` prefix the overview/status SSE invalidations touch.
export interface CameraUrlsOut {
  stream_url: string;
  snapshot_url: string;
  expires_at: string;
}

export interface CameraStatusOut {
  status: string;
  source: "a1" | "rtsp" | "external" | "none";
  beta: boolean;
  rotation: number;
  default_fps?: number;
}

export interface OverlayTokenOut {
  id: number;
  printer_id: number;
  label: string | null;
  created_at: string;
  revoked_at: string | null;
}

export interface OverlayTokenCreatedOut extends OverlayTokenOut {
  token: string;
  overlay_url: string;
}

export interface OverlayOut {
  printer_name: string;
  gcode_state: string | null;
  progress: number | null;
  layer: number | null;
  total_layers: number | null;
  remaining_min: number | null;
  nozzle_temp: number | null;
  bed_temp: number | null;
  subtask_name: string | null;
  rotation: number;
  stream_url: string;
  snapshot_url: string;
}

export type PrintJobState =
  | "queued"
  | "uploading"
  | "starting"
  | "printing"
  | "paused"
  | "finished"
  | "failed"
  | "canceled";

export interface PrintJobOut {
  id: number;
  printer_id: number;
  file_id: number | null;
  origin: PrintJobOrigin;
  subtask_name: string | null;
  state: PrintJobState;
  progress_pct: number | null;
  remaining_min: number | null;
  layer: number | null;
  total_layers: number | null;
  printer_error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

// -- print plan / send (backend/app/schemas/print_flow.py, P5 Task 7) ------

export type PlanFilamentStatus = "ok" | "color" | "type" | "none";

export interface PlanIn {
  file_id: number;
  plate?: number | null;
  printer_id?: number | null;
  overrides?: Record<number, number>;
}

export interface PlanFilament {
  filament_id: number;
  type: string | null;
  color: string | null;
  grams: number | null;
  tray: number | null;
  status: PlanFilamentStatus;
  delta_e: number | null;
  spool: SpoolBrief | null;
}

export interface PlanPrinter {
  id: number;
  name: string;
  model: string | null;
  idle: boolean;
  compatible: boolean;
  reason: string | null;
}

export interface PlanOptions {
  bed_levelling: boolean;
  flow_cali: boolean;
  timelapse: boolean;
}

export interface PlanOut {
  file_id: number;
  model_id: number | null;
  plate: number | null;
  plates: PlateOut[];
  printer_id: number | null;
  printers: PlanPrinter[];
  filaments: PlanFilament[];
  ams_mapping: number[];
  use_ams: boolean;
  est_duration_s: number | null;
  est_grams: number | null;
  est_cost: number | null;
  warnings: string[];
  options: PlanOptions;
}

export interface PrintRequest {
  file_id: number;
  plate?: number;
  subtask_name?: string | null;
  use_ams?: boolean;
  ams_mapping?: number[];
  bed_levelling?: boolean;
  flow_cali?: boolean;
  timelapse?: boolean;
}

export interface SendIn extends PrintRequest {
  printer_id: number;
}

export interface PrintJobUpdatedEvent {
  type: "print_job.updated";
  print_job_id: number;
  printer_id: number;
  state: PrintJobState;
}

export type AppEvent = JobUpdatedEvent | PrintJobUpdatedEvent;

// -- imports (backend/app/schemas/imports.py, app/models/enums.py) --------

export type ImportSite = "thingiverse" | "printables" | "makerworld";
export type ImportState = "pending" | "fetching" | "downloading" | "done" | "failed";

export interface ImportOut {
  id: number;
  url: string;
  site: ImportSite;
  external_id: string | null;
  state: ImportState;
  model_id: number | null;
  error: string | null;
  meta: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface ImportCreate {
  url: string;
}

export interface ImportTokensIn {
  thingiverse_token: string;
  makerworld_token: string;
}

// "***" when set, "" otherwise
export interface ImportTokensOut {
  thingiverse_token: string;
  makerworld_token: string;
}

// Imports reuse the existing `job.updated` shape (JobUpdatedEvent above) --
// no new event type; only useEvents.tsx's handler gains an
// `import_from_url` branch.

// `GET /imports/search` (Workstream B task B1) -- browse-then-import search
// results for a single site.
export interface SearchResult {
  site: ImportSite;
  external_id: string;
  title: string;
  url: string;
  author: string | null;
  thumbnail_url: string | null;
}

// Per-site outcome of a federated `GET /imports/search` (M8 E1): counts,
// whether another page likely exists, and errors, so the UI can label each
// site and surface which upstream failed or needs a token.
export interface SiteSearchStatus {
  site: ImportSite;
  count: number;
  has_more: boolean;
  status: "ok" | "error";
  detail: string | null;
}

export interface SearchResponse {
  results: SearchResult[];
  per_site: SiteSearchStatus[];
}

// -- followed collections + review queue (M8 H) ----------------------------

export type CollectionSyncMode = "auto" | "review";

/** One of the signed-in user's lists on a site (`GET /imports/lists`). */
export interface RemoteList {
  site: ImportSite;
  list_id: string;
  kind: string;
  title: string;
  count: number | null;
}

export interface FollowedCollection {
  id: number;
  site: ImportSite;
  list_id: string;
  kind: string;
  title: string;
  mode: CollectionSyncMode;
  last_synced_at: string | null;
  last_error: string | null;
  created_at: string;
  /** Up to 4 member-model thumbnail URLs for the card's 2x2 collage. */
  preview_thumbnails: string[];
}

/** An item a `review`-mode sync found but didn't import. */
export interface PendingImport {
  id: number;
  collection_id: number;
  site: ImportSite;
  external_id: string;
  title: string;
  url: string;
  thumbnail_url: string | null;
  created_at: string;
  // R7 T1: the collection this item should be GROUPED/DISPLAYED under,
  // resolved server-side from real membership (often more specific than the
  // stamped `collection_id` above -- see `PendingImportOut` in
  // backend/app/schemas/collections.py).
  group_collection_id: number;
  group_title: string;
}

// -- Bambu Lab account (backend/app/schemas/settings.py, Workstream B task
// B2) -- connecting an account powers MakerWorld's authenticated search +
// file downloads; anonymous MakerWorld access only sees trending results
// and can't download files (see backend/app/services/bambu_auth.py).
// NEITHER request nor response type below ever carries a token.

export type BambuRegion = "global" | "china";

export interface BambuStatusOut {
  connected: boolean;
  account: string | null;
  region: BambuRegion;
  // True iff connected AND the stored refresh token's last refresh attempt
  // failed (import-health task) -- the expiry-banner UX signal so the
  // frontend can tell "not configured" apart from "configured but the
  // session died" (backend/app/schemas/settings.py's BambuStatusOut).
  needs_reconnect: boolean;
}

export interface BambuLoginIn {
  account: string;
  password: string;
  region: BambuRegion;
}

export interface BambuVerifyIn {
  account: string;
  code: string;
  region: BambuRegion;
  mfa_context: Record<string, unknown>;
}

// Shared by both /settings/bambu/login and /settings/bambu/verify --
// `status` is "connected" or "mfa_required"; `mfa_context` is the opaque
// continuation to echo back into a follow-up verify call when MFA is
// required (never a secret value itself).
export interface BambuLoginOut {
  status: "connected" | "mfa_required";
  account: string | null;
  region: BambuRegion | null;
  mfa_context: Record<string, unknown> | null;
}

// -- Printables account (backend/app/schemas/settings.py, Workstream A task
// A1) -- connecting an account is what makes the Saved tab's Printables
// collections/likes sync possible (see backend/app/services/
// printables_auth.py). Printables has no login flow this app can drive
// itself, so "connect" is pasting the browser's `auth.refresh_token` cookie
// value. NEITHER request nor response type below ever carries a token.

export interface PrintablesStatusOut {
  connected: boolean;
  username: string | null;
  user_id: string | null;
}

export interface PrintablesConnectIn {
  refresh_token: string;
}

// -- browser-extension API tokens (backend/app/schemas/settings.py, M10
// Workstream A) -- session-gated mint/list/revoke of the SEPARATE
// bearer-token auth plane the sideloaded extension uses. `ApiTokenMintOut`
// is the ONE place the plaintext token is ever present in a response;
// `ApiTokenOut` (the list shape) never carries the token or its hash.

export interface ApiTokenCreateIn {
  label: string;
}

export interface ApiTokenMintOut {
  id: number;
  label: string;
  token: string;
  created_at: string;
}

export interface ApiTokenOut {
  id: number;
  label: string;
  created_at: string;
  last_used_at: string | null;
}

// -- print history (backend/app/schemas/prints.py, Branch 5 Task 1) --------
// A user-entered log of print attempts, distinct from the print queue's
// worklist (`QueueEntry` below) and `print_jobs`' live send-to-printer
// telemetry (M4, `PrintJobOut` above).

export type PrintResult = "success" | "fail" | "partial";

// -- materials (R13c) -------------------------------------------------------
// Filament profiles a print can reference (`PrintEntry.material_id`), in
// addition to the freeform `filament` text note.

// Known kind values offered by the material form's `Select` (backend's
// `MaterialCreate`/`MaterialUpdate`/`MaterialOut.kind` are a plain
// `str | None`, not DB/pydantic-enforced against this list -- it's a
// client-side convenience list, not the wire type).
export type MaterialKind = "PLA" | "PETG" | "ABS" | "ASA" | "TPU" | "Resin" | "Other";

export interface MaterialOut {
  id: number;
  name: string;
  kind: string | null;
  color: string | null;
  vendor: string | null;
  notes: string | null;
  print_count: number;
}

export interface MaterialCreate {
  name: string;
  kind?: string | null;
  color?: string | null;
  vendor?: string | null;
  notes?: string | null;
}

export interface MaterialPatch {
  name?: string;
  kind?: string | null;
  color?: string | null;
  vendor?: string | null;
  notes?: string | null;
}

// The nested `material` a `PrintEntry` carries (`backend/app/schemas/
// prints.py`'s `PrintMaterialOut`) -- a smaller shape than `MaterialOut`
// (no `vendor`/`notes`/`print_count`).
export interface PrintMaterialOut {
  id: number;
  name: string;
  kind: string | null;
  color: string | null;
}

export type PrintJobOrigin = "app" | "external";
export type ArchiveResult = "completed" | "failed" | "canceled" | "stopped";
export type FetchState = "pending" | "done" | "missing" | "error";
export type PrintQuality = "good" | "reject";
export type PrintSource = "manual" | "printer" | "external";

/** One per-tray estimated filament row on an archived run. */
export interface EstFilament {
  /** Slicer filament index. */
  slot?: number | null;
  /** Mapped AMS tray, when the job carried an ams_mapping. */
  tray?: number | null;
  type?: string | null;
  color?: string | null;
  grams?: number | null;
  [key: string]: unknown;
}

export interface PrintEntry {
  id: number;
  model_id: number | null;
  print_job_id: number | null;
  source: PrintSource;
  quality: PrintQuality | null;
  failure_reason: string | null;
  filament_cost: number | null;
  machine_cost: number | null;
  energy_kwh: number | null;
  energy_cost: number | null;
  total_cost: number | null;
  created_by_user_id: number | null;
  printed_at: string;
  printer_name: string | null;
  filament: string | null;
  filament_g: number | null;
  result: PrintResult;
  duration_min: number | null;
  notes: string | null;
  created_at: string;
  // R13c: optional structured material reference alongside the freeform
  // `filament` text fallback (mirrors backend `PrintMaterialOut`).
  material_id: number | null;
  material: PrintMaterialOut | null;
  // R13c: populated only by `GET /stats`'s `recent_prints` (backend's
  // `PrintOut.model_slug`/`model_name`) -- `null` for every other caller
  // (per-model listing already scopes to one model).
  model_slug: string | null;
  model_name: string | null;
}

// `POST /models/{model_id}/prints` payload -- an omitted `printed_at` lets
// the server default to now (its own `server_default=now()`), and an
// omitted `result` defaults to `"success"` server-side.
export interface PrintCreateIn {
  printed_at?: string;
  printer_name?: string | null;
  material_id?: number | null;
  filament?: string | null;
  filament_g?: number | null;
  result?: PrintResult;
  duration_min?: number | null;
  notes?: string | null;
}

// `PATCH /prints/{print_id}` payload -- all fields optional; only the ones
// present are applied (backend's `exclude_unset` patch semantics, same as
// `NotePatch`/`ModelPatch`).
export interface PrintPatchIn {
  printed_at?: string;
  printer_name?: string | null;
  material_id?: number | null;
  filament?: string | null;
  filament_g?: number | null;
  result?: PrintResult;
  duration_min?: number | null;
  notes?: string | null;
  quality?: PrintQuality | null;
  failure_reason?: string | null;
}

// -- print archive (backend/app/schemas/archives.py, P2) -------------------

export interface ArchiveOut {
  id: number;
  print_job_id: number;
  printer_id: number;
  printer_name: string;
  origin: PrintJobOrigin;
  subtask_name: string | null;
  model_id: number | null;
  model_slug: string | null;
  model_name: string | null;
  revision_id: number | null;
  result: ArchiveResult;
  duration_s: number | null;
  est_filament: EstFilament[] | null;
  plate: number | null;
  fetch_state: FetchState;
  fetch_error: string | null;
  has_thumbnail: boolean;
  has_file: boolean;
  has_snapshot: boolean;
  has_timelapse: boolean;
  media_note: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  created_by_user_id: number | null;
  run: PrintEntry | null;
  // T7 archive extras (optional so older fixtures still type-check)
  recorded_result?: ArchiveResult;
  result_override?: ArchiveResult | null;
  notes?: string | null;
  tags?: string[];
  confirm_requested?: boolean;
  confirm_expires_at?: string | null;
  user_verdict?: ArchiveVerdict | null;
  user_verdict_source?: VerdictSource | null;
  verdict_at?: string | null;
  verdict_by_user_id?: number | null;
  photos?: ArchivePhoto[];
}

// -- print queue (backend/app/schemas/queue.py, Branch 4 Task 1) -----------
// An ordered "models to print" worklist. `position` is a dense 1-based rank
// over the whole queue, renumbered on every insert/delete/reorder.

export type QueueState = "waiting" | "slicing" | "ready" | "dispatched" | "held" | "failed" | "done";

export interface QueueEntry {
  id: number;
  model_id: number;
  position: number;
  added_at: string;
  model: ModelSummary;
  // Round 8 Task 3: the newest sliced `.gcode.3mf` file on the model's
  // current revision, if any -- lets the queue row offer a "Print" action
  // directly. `null` when the model has no current revision, or none of its
  // current-revision files are a sendable gcode_3mf yet.
  printable_file: FileOut | null;
  // P6 Task 6/9: per-file dispatch columns (`backend/app/schemas/queue.py`'s
  // `QueueEntryOut`) -- `printer_id` is the requested target (`null` =
  // "any printer"/unassigned lane), `assigned_printer_id` is set once the
  // dispatcher claims the row (state `dispatched` and later).
  printer_id?: number | null;
  recipe_id?: number | null;
  recipe_name?: string | null;
  file_id?: number | null;
  sliced_file_id?: number | null;
  plate?: number | null;
  copies?: number;
  state?: QueueState;
  assigned_printer_id?: number | null;
  print_job_id?: number | null;
  attempts?: number;
  last_error?: string | null;
  file_name?: string | null;
  // P7 review/billing plan (`backend/app/schemas/queue.py`'s
  // `QueueEntryOut`): review gate + billing columns.
  cost_center_id?: number | null;
  review_state?: ReviewState;
  reviewed_by?: number | null;
  reviewed_at?: string | null;
  review_note?: string | null;
  reserved_cost?: number | null;
  hold_reason?: string | null;
  // P8 Task 7: this entry's printer target is being held off by a firing
  // HA alert link (`hold_queue=true`) -- distinct from `hold_reason` above
  // (the review/billing hold), both can be set independently.
  printer_hold_reason?: string | null;
  // M6: who queued it, for the review inbox.
  created_by?: { id: number; username: string } | null;
}

export interface QueueEnqueueBody {
  model_id?: number | null;
  file_id?: number | null;
  plate?: number | null;
  printer_id?: number | null;
  recipe_id?: number | null;
  copies?: number;
  ams_mapping?: number[] | null;
  options?: PlanOptions | null;
  front?: boolean;
  cost_center_id?: number | null;
}

// -- review gate (backend/app/models/enums.py `ReviewState`, P7 review/
// billing plan) ------------------------------------------------------------

export type ReviewState = "none" | "pending" | "approved" | "rejected";

export interface ReviewIn {
  action: "approve" | "reject";
  note?: string | null;
}

export interface ReviewCountOut {
  count: number;
}

// -- finance (backend/app/schemas/finance.py, P7 review/billing plan Task
// 5): billing config, cost centers, ledger, deposits/adjustments. Money is
// always a plain JSON number at 2dp -- never a string. -------------------

export type LedgerKind = "deposit" | "adjustment" | "charge" | "reservation" | "release";

export interface FinanceConfig {
  billing_enabled: boolean;
}

export interface CostCenter {
  id: number;
  name: string;
  owner_user_id: number | null;
  group_id: number | null;
  budget_total: number | null;
  budget_monthly: number | null;
  balance: number;
  open_reserved: number;
  month_spent: number;
  available: number | null;
  active: boolean;
}

export interface CostCenterCreateIn {
  name: string;
  owner_user_id?: number | null;
  group_id?: number | null;
  budget_total?: number | null;
  budget_monthly?: number | null;
}

export interface CostCenterPatchIn {
  name?: string;
  budget_total?: number | null;
  budget_monthly?: number | null;
  active?: boolean;
}

export interface LedgerEntry {
  id: number;
  cost_center_id: number;
  kind: LedgerKind;
  amount: number;
  print_queue_id: number | null;
  print_id: number | null;
  created_by_user_id: number | null;
  note: string | null;
  created_at: string;
}

export interface DepositIn {
  cost_center_id: number;
  amount: number;
  kind?: LedgerKind;
  note?: string | null;
}

// -- printers overview (backend/app/schemas/printers.py, P3 Task 1) --------
// One-call dashboard payload: every visible printer's live status + active
// job + recent history, plus the queue's head and cross-printer recent runs.

/** A `PrintJobOut` resolved onto its file's model. All three fields are
 * `null` when the job has no `file_id` (an `external` job) or the file's
 * revision/model was since deleted. */
export interface ActiveJobOut extends PrintJobOut {
  plate: number | null;
  model_id: number | null;
  model_slug: string | null;
  model_name: string | null;
  /** Plate thumbnail of the job's stored 3MF, else null. */
  thumbnail_url?: string | null;
}

export interface PrinterOverviewItem {
  printer: PrinterOut;
  status: PrinterStatusOut;
  active_job: ActiveJobOut | null;
  last_runs: ArchiveOut[];
  can_print: boolean;
  can_control: boolean;
  // P6 Task 8: up to 3 non-terminal queue entries targeting THIS printer
  // (`printer_id == id` or, once dispatched, `assigned_printer_id == id`).
  up_next: QueueEntry[];
  // P8 Task 7: this printer's currently-firing HA alert, if any, and
  // whether that alert is ALSO holding the dispatcher off this printer
  // (`hold_queue=true` on the firing link) -- distinct from merely being
  // reported.
  ha_alert?: string | null;
  ha_held?: boolean;
}

export interface PrintersOverviewOut {
  printers: PrinterOverviewItem[];
  up_next: QueueEntry[];
  recent_runs: ArchiveOut[];
}

// -- duplicate-files report (backend/app/schemas/reports.py, Branch 4 Task 1)
// Files sharing a blob hash across more than one model -- reclaimable
// storage from the same content having been imported/uploaded more than once.

export interface DuplicateFile {
  model_id: number;
  model_slug: string;
  model_name: string;
  model_archived: boolean;
  file_id: number;
  file_name: string;
  // Round 11 T4: whether this copy lives on its model's CURRENT revision --
  // a copy on a superseded revision can't be deleted (file ops are
  // current-revision-only, same guard as `DELETE /files/{id}`), so the
  // duplicates page excludes it from keeper/deletable choices.
  is_current_revision: boolean;
}

export interface DuplicateGroup {
  blob_hash: string;
  size: number;
  wasted_bytes: number;
  files: DuplicateFile[];
}

export interface DuplicatesReport {
  groups: DuplicateGroup[];
  total_wasted_bytes: number;
}

// `POST /reports/duplicates/resolve` (Round 11 T2): the client picks a
// "keeper" file per duplicate group and the server deletes every other copy
// in that group.

export interface KeepChoice {
  blob_hash: string;
  file_id: number;
}

/** Why a copy the request named wasn't deleted (backend
 * `try_delete_duplicate_copy` + `resolve_duplicates`). A union, not a bare
 * string, mirroring `SkipReason` in backend/app/schemas/reports.py: the page
 * treats these differently (an old-revision skip was never promised, so it
 * doesn't warrant a warning toast), and a renamed reason should break the
 * build rather than quietly slip past that check. */
export type SkipReason = "not_found" | "not_current_revision" | "store_pending" | "keeper_missing";

export interface SkippedCopy {
  file_id: number;
  reason: SkipReason;
}

export interface DuplicatesResolveOut {
  deleted: number;
  reclaimed_bytes: number;
  skipped: SkippedCopy[];
}

// -- storage tree browser (R13b, `GET /storage/tree?path=`) ----------------
// Plain-file-browser navigation over the raw storage layout: `dirs` are the
// immediate subdirectories of `path` (each with its own file/model counts),
// `files` are the raw files that live directly under `path`, and `model` is
// non-null when `path` is itself a single model's own directory (its
// summary, for the header strip above the file list).

export interface StorageTreeDir {
  name: string;
  path: string;
  file_count: number;
  model_count: number;
}

export interface StorageTreeFile {
  id: number;
  name: string;
  rel_path: string;
  size: number;
  kind: BlobKind;
  format: BlobFormat;
  model_slug: string;
  blob_hash: string;
  revision_id: number;
}

export interface StorageTreeOut {
  path: string;
  dirs: StorageTreeDir[];
  files: StorageTreeFile[];
  model: ModelSummary | null;
}

// --- P1 admin (users, groups, OIDC providers) ---

export type UserRole = "admin" | "member" | "viewer";

export interface GroupRef {
  id: number;
  name: string;
}

export interface UserAdminOut {
  id: number;
  username: string;
  display_name: string | null;
  email: string | null;
  role: UserRole;
  is_active: boolean;
  has_password: boolean;
  oidc_linked: boolean;
  last_login_at: string | null;
  groups: GroupRef[];
}

export interface UserCreateIn {
  username: string;
  password?: string | null;
  display_name?: string | null;
  email?: string | null;
  role?: UserRole;
}

export interface UserPatchIn {
  display_name?: string | null;
  email?: string | null;
  role?: UserRole;
  is_active?: boolean;
}

export interface PrinterAccessIO {
  printer_id: number;
  can_print: boolean;
  can_control: boolean;
  requires_review: boolean;
}

export interface GroupMemberOut {
  user_id: number;
  username: string;
  source: string;
}

export interface GroupOut {
  id: number;
  name: string;
  description: string | null;
  permissions: string[];
  members: GroupMemberOut[];
  printer_access: PrinterAccessIO[];
}

export interface GroupCreateIn {
  name: string;
  description?: string | null;
  permissions?: string[];
}

export interface GroupPatchIn {
  name?: string;
  description?: string | null;
  permissions?: string[];
}

export interface OidcProviderOut {
  id: number;
  name: string;
  slug: string;
  discovery_url: string;
  client_id: string;
  has_client_secret: boolean;
  scopes: string;
  groups_claim: string;
  group_mapping: Record<string, number>;
  admin_group: string | null;
  auto_create_users: boolean;
  link_by_email: boolean;
  enabled: boolean;
  trusts_idp_mfa: boolean;
  /** Server-computed; display as-is, never rebuild from window.location. */
  callback_url: string;
}

export interface OidcProviderCreateIn {
  name: string;
  slug: string;
  discovery_url: string;
  client_id: string;
  client_secret: string;
  scopes?: string;
  groups_claim?: string;
  group_mapping?: Record<string, number>;
  admin_group?: string | null;
  auto_create_users?: boolean;
  link_by_email?: boolean;
  enabled?: boolean;
  trusts_idp_mfa?: boolean;
}

export type OidcProviderPatchIn = Partial<OidcProviderCreateIn>;

// -- slicer profiles, recipes and sidecar health (backend/app/schemas/
// slicing.py, P6 "slicer, profiles and dispatch" plan) -------------------

export type ProfileKind = "printer" | "process" | "filament";
export type ProfileSource = "import" | "builtin" | "synced";
export type Fanout = "single" | "spread" | "first_n";

export interface SlicerProfile {
  id: number;
  name: string;
  kind: ProfileKind;
  printer_model: string | null;
  nozzle_mm: number | null;
  source: ProfileSource;
  created_by_user_id: number | null;
  created_at: string;
  updated_at: string;
  // Only present on `GET /profiles/{id}` or `GET /profiles?full=1`.
  data?: Record<string, unknown> | null;
}

export interface ProfileImportResult {
  created: number;
  updated: number;
  /** Rows a sidecar sync left alone because they're imported or user-edited. */
  skipped?: number;
}

export interface PrintRecipe {
  id: number;
  name: string;
  printer_profile_id: number;
  process_profile_id: number;
  filament_profile_ids: (number | null)[];
  bed_type: string;
  options: Record<string, unknown>;
  target_printer_id: number | null;
  target_printer_model: string | null;
  fanout: Fanout;
  n: number | null;
  created_by_user_id: number | null;
  created_at: string;
  /** Only set when listed with `?printer_id=`; null/absent otherwise. */
  compatible?: boolean | null;
  incompatible_reason?: string | null;
}

export interface PrintRecipeIn {
  name: string;
  printer_profile_id: number;
  process_profile_id: number;
  filament_profile_ids?: (number | null)[];
  bed_type: string;
  options?: Record<string, unknown>;
  target_printer_id?: number | null;
  target_printer_model?: string | null;
  fanout?: Fanout;
  n?: number | null;
}

export interface SlicerHealth {
  configured: boolean;
  ok: boolean;
  version?: string | null;
  error?: string | null;
}

// -- Home Assistant settings (backend/app/schemas/home_assistant.py, P8
// Task 7) -- connection config (secrets write-only, "***" sentinel
// convention), the two connection tests, and `ha_links` CRUD. -----------

export type HaRole = "energy_kwh" | "power_w" | "door" | "smoke" | "temp" | "humidity";

export type HaConditionOp = "eq" | "ne" | "gt" | "gte" | "lt" | "lte" | "in";

export interface HaCondition {
  op: HaConditionOp;
  value: string | number | (string | number)[];
}

export interface HaBridgeStatus {
  connected: boolean;
  error: string | null;
  actor_ok?: boolean;
  actor_error?: string | null;
  at: number;
}

export interface HaSettings {
  ha_mqtt_url: string | null;
  ha_mqtt_user: string | null;
  has_mqtt_password: boolean;
  ha_discovery_prefix: string;
  ha_url: string | null;
  has_token: boolean;
  ha_actor_user_id: number | null;
  ha_actor_username: string | null;
  actor_ok: boolean;
  actor_error: string | null;
  pull_status: { ok: boolean; errors?: string[]; checked_at?: string; [key: string]: unknown } | null;
  bridge_status: HaBridgeStatus | null;
}

// All fields optional (sentinel-merge): omitted keeps, for a secret field
// "" clears and anything else (re)sets it. Also reused, unsaved, as the
// override body for the two connection-test endpoints.
export interface HaSettingsIn {
  ha_mqtt_url?: string | null;
  ha_mqtt_user?: string | null;
  ha_mqtt_password?: string | null;
  ha_discovery_prefix?: string | null;
  ha_url?: string | null;
  ha_token?: string | null;
  ha_actor_user_id?: number | null;
}

export interface HaActor {
  id: number;
  username: string;
}

export interface HaTestOut {
  ok: boolean;
  detail: string;
}

export interface HaLink {
  id: number;
  printer_id: number | null;
  printer_name: string | null;
  entity_id: string;
  role: HaRole;
  alert_condition: HaCondition | null;
  hold_queue: boolean;
  pause_print: boolean;
  created_by_user_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface HaLinkIn {
  printer_id?: number | null;
  entity_id: string;
  role: HaRole;
  alert_condition?: HaCondition | null;
  hold_queue?: boolean;
  pause_print?: boolean;
}

export type HaLinkPatchIn = Partial<HaLinkIn>;

// `/api/virtual-printers` (backend/app/schemas/virtual_printers.py, Q11).
export type VirtualPrinterMode = "queue" | "review" | "archive" | "proxy";

export interface VirtualPrinter {
  id: number;
  name: string;
  model: string;
  serial: string;
  access_code: string;
  mode: VirtualPrinterMode;
  target_printer_id: number | null;
  enabled: boolean;
  created_by_user_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface VirtualPrinterIn {
  name: string;
  model: string;
  mode: VirtualPrinterMode;
  target_printer_id?: number | null;
  enabled?: boolean;
}

export interface VirtualPrinterPatchIn extends Partial<VirtualPrinterIn> {
  regenerate_access_code?: boolean;
}

// Q5 (`/printers/{id}/hms|firmware|diagnostic`, `/printers/discover`).
export interface HmsFault {
  code: string;
  full_code: string;
  kind: "hms" | "print_error";
  severity: string;
  severity_level: number;
  module: string;
  description: string | null;
  wiki_url: string;
  actions: string[];
}

export interface FirmwareInfo {
  model_supported: boolean;
  current: string | null;
  latest: string | null;
  release_notes: string | null;
  release_time: string | null;
  checked_at: string | null;
  download_available: boolean;
  update_available: boolean;
  upload: { status?: string; version?: string } | null;
}

export interface DiagnosticCheck {
  id: string;
  status: "ok" | "fail" | "skip";
  hint: string;
  detail: string | null;
}

export interface DiagnosticReport {
  ok: boolean;
  checks: DiagnosticCheck[];
}

export interface DiagnosticIn {
  host: string;
  serial: string;
  access_code: string;
  model?: string | null;
}

export interface DiscoveredPrinter {
  host: string;
  serial: string;
  model_hint: string | null;
  source: "ssdp" | "sweep";
}

// --- Q7: maintenance, smart plugs, backups, updates ---
export type MaintenanceStatus = "ok" | "soon" | "overdue";
export interface MaintenanceTask {
  id: number;
  printer_id: number;
  name: string;
  interval_hours: number | null;
  interval_days: number | null;
  last_done_at: string | null;
  notes: string | null;
  status: MaintenanceStatus;
  hours_since: number | null;
  days_since: number | null;
  hours_remaining: number | null;
  days_remaining: number | null;
}
export interface MaintenanceTaskIn {
  name: string;
  interval_hours?: number | null;
  interval_days?: number | null;
  notes?: string | null;
}
export interface MaintenanceLog {
  id: number;
  task_id: number;
  done_at: string;
  hours_at: number | null;
  note: string | null;
  created_by_user_id: number | null;
}
export type PlugKind = "tasmota" | "ha" | "mqtt" | "rest";
export interface PlugOut {
  printer_id: number;
  kind: PlugKind;
  config: Record<string, unknown>;
  auto_off_after_min: number | null;
  auto_on_before_print: boolean;
  last_state: string | null;
  last_power_w: number | null;
  last_polled_at: string | null;
  last_error: string | null;
}
export interface PlugIn {
  kind: PlugKind;
  config: Record<string, unknown>;
  auto_off_after_min: number | null;
  auto_on_before_print: boolean;
}
export interface BackupRun {
  id: number;
  kind: string;
  status: string;
  size_bytes: number | null;
  location: string | null;
  git_pushed: boolean;
  error: string | null;
  created_by_user_id: number | null;
  created_at: string;
  finished_at: string | null;
}
export type GitProvider = "github" | "gitlab" | "gitea" | "forgejo";
export interface OpsSettings {
  backup_enabled: boolean;
  backup_interval_hours: number;
  backup_retention: number;
  git_provider: GitProvider | null;
  git_base_url: string | null;
  git_repo: string | null;
  git_branch: string;
  git_token_set: boolean;
  update_check_enabled: boolean;
}
export type OpsSettingsIn = Partial<Omit<OpsSettings, "git_token_set">> & { git_token?: string };
export interface UpdateInfo {
  latest: string | null;
  current: string;
  url: string | null;
  notes: string | null;
  update_available: boolean;
  checked_at: string | null;
  error: string | null;
}

// ---- T7 archive extras ----

export type ArchiveVerdict = "good" | "bad";
export type VerdictSource = "dialog" | "link" | "plate_clear" | "card" | "api";

export interface ArchivePhoto {
  filename: string;
  kind: "photo" | "timelapse";
  uploaded_by: number | null;
  at: string;
}

export interface ArchivePatchIn {
  notes?: string | null;
  tags?: string[];
  result_override?: ArchiveResult | null;
}

export interface CompareOut {
  archives: ArchiveOut[];
  settings_diff: { key: string; values: Record<string, unknown> }[];
  filament: Record<string, EstFilament[] | null>;
  time: Record<string, { estimated_s: number | null; actual_s: number | null }>;
  correlation: { key: string; value: unknown; success_rate: number; n: number }[];
}

export interface CalendarDay {
  date: string;
  count: number;
  ok: number;
  failed: number;
  hours: number;
}

export interface ArchivePurgeSettings {
  enabled: boolean;
  older_than_days: number;
  keep_failed: boolean;
  keep_with_verdict: boolean;
}
export interface PurgePreview {
  count: number;
  bytes: number;
}
export interface PurgeRunOut {
  deleted: number;
  bytes: number;
}
export interface PurgeStats {
  last_run_at: string | null;
  last_deleted: number;
  last_bytes: number;
  next_run: string | null;
}

export interface ConfirmSettings {
  enabled: boolean;
  external_prints: boolean;
  default_outcome: ArchiveVerdict | null;
  default_after_h: number;
  default_good_on_plate_clear: boolean;
}

export interface TimelapseInfo {
  duration_s: number;
  fps: number;
  width: number;
  height: number;
  bytes: number;
}
export interface TimelapseProcessIn {
  trim_start: number;
  trim_end?: number;
  speed: number;
  save_mode: "replace" | "new";
}
export interface TimelapseJobState {
  state: "pending" | "running" | "done" | "error";
  error: string | null;
  filename: string | null;
}

// ---- T5 spool housekeeping ----
export interface Supplier {
  id: number;
  name: string;
  website: string | null;
  customer_number: string | null;
  note: string | null;
  created_at: string;
  spool_count?: number;
}
export interface SupplierIn {
  name: string;
  website?: string | null;
  customer_number?: string | null;
  note?: string | null;
}

export interface SpoolSupplierLink {
  supplier_id: number;
  supplier_name?: string;
  article_number: string | null;
  quoted_price_per_kg: number | null;
  is_purchase_source: boolean;
}

export interface LocationSensor {
  id: number;
  location_id: number;
  name: string;
  entity_id: string;
  kind: string;
  device_class: string | null;
  unit: string | null;
  alert_above: number | null;
  alert_below: number | null;
  notify_on_alert: boolean;
  show_on_card: boolean;
  sort_order: number;
  last_state: string | null;
  last_changed_at: string | null;
  alerting: boolean;
}
export type SensorKind = "temperature" | "humidity" | "other";
export interface LocationSensorIn {
  name: string;
  entity_id: string;
  kind?: SensorKind;
  alert_above?: number | null;
  alert_below?: number | null;
  notify_on_alert?: boolean;
  show_on_card?: boolean;
  sort_order?: number;
}
export interface HaEntity {
  entity_id: string;
  friendly_name: string | null;
  device_class: string | null;
  unit: string | null;
  state: string | null;
}
export interface SensorReading {
  sensor_id: number;
  name?: string;
  kind?: string;
  entity_id?: string;
  state: string | null;
  unit: string | null;
  alerting: boolean;
  last_changed_at: string | null;
}
export interface StorageLocation {
  id: number;
  name: string;
  identifier: string | null;
  created_at: string;
  spool_count?: number;
  sensors?: LocationSensor[];
}
export interface StorageLocationIn {
  name: string;
  identifier?: string | null;
}

export interface ColorCatalogEntry {
  id: number;
  manufacturer: string;
  color_name: string;
  hex: string;
  material: string | null;
  is_default: boolean;
  extra_colors: string[] | null;
  effect_type: string | null;
}
export interface ColorCatalogIn {
  manufacturer: string;
  color_name: string;
  hex: string;
  material?: string | null;
  extra_colors?: string[];
  effect_type?: string | null;
}

export interface BulkSpoolUpdate {
  material_type?: string;
  brand?: string | null;
  color_hex?: string | null;
  color_name?: string | null;
  location_id?: number | null;
  cost_per_kg?: number | null;
  low_stock_g?: number;
  label_weight_g?: number;
}
export interface BulkResult {
  results: Array<{ id: number; ok: boolean; error?: string | null }>;
  not_found: number[];
}

export interface CsvImportRow {
  line: number;
  action: "create" | "update" | "error" | "skipped";
  spool_id?: number | null;
  errors: string[];
  warnings: string[];
}
export interface CsvImportPreview {
  token: string | null;
  rows: CsvImportRow[];
  summary: { create: number; update: number; error: number; skipped?: number };
  warnings?: string[];
}
export interface CsvImportResult {
  created: number;
  updated: number;
  errors: number;
}

export type LabelTemplate = "tight" | "roomy";
export type LabelPage = "a4" | "letter" | "roll_62mm";
export type LabelFormat = "pdf" | "png";
export interface LabelRequest {
  ids: number[];
  template: LabelTemplate;
  page: LabelPage;
}

// ---- T3 printer ops ----
export type XcamSensitivity = "low" | "medium" | "high" | "never_halt";
export interface XcamState {
  enable: boolean | null;
  /** The printer reports only low|medium|high; `never_halt` is local-only. */
  sensitivity?: XcamSensitivity | null;
}
export interface PrintOptions {
  xcam?: Record<string, XcamState>;
  print_halt?: boolean | null;
  auto_recovery?: boolean | null;
  auto_switch_filament?: boolean | null;
}
export type PrintOptionKey = "auto_recovery" | "auto_switch_filament";

export interface SlotConfigIn {
  tray_info_idx: string;
  tray_type: string;
  sub_brand: string;
  /** RRGGBB[AA] without "#". */
  color: string;
  nozzle_min: number;
  nozzle_max: number;
  apply_device: boolean;
}

export interface RemoteFile {
  name: string;
  /** Full path on the SD card, e.g. "/model/a.3mf". */
  path: string;
  size: number | null;
  mtime: string | null;
  is_dir: boolean;
}
export interface FileStorage {
  total?: number | null;
  used?: number | null;
  free?: number | null;
  source?: string | null;
  present?: boolean | null;
}
export type SlotDeviceStatus = "applied" | "pending" | "skipped" | "failed";
export interface SlotConfigOut {
  trays: unknown[];
  device: { status: SlotDeviceStatus; [k: string]: unknown };
}
