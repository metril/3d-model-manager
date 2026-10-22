# P5 Spool Inventory, AMS Mapping and Print Dialog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (or superpowers:executing-plans) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Spools are tracked as inventory. RFID spools assign themselves to AMS slots, and every archived run decrements the spools it used (from the 3MF's per-tray grams, or from the AMS remain% delta as a fallback). Run cost uses each spool's price. One modal (`PrintDialog`) replaces `SendToPrinterDialog`: it auto-maps filaments to slots, warns on mismatches, and sends the print or queues it. Reprint is real.

**Architecture:** Two pure modules: `services/ams_match.py` (type → OKLab ΔE → override) and the math half of `services/spool_inventory.py`. The DB half of `spool_inventory` covers slot assignment, RFID auto-assign, usage recording and slot maps. Adapter trays gain `ams_unit/tray/tray_uuid/remain_pct/tray_type` plus the external spool. printerd snapshots trays at job start and at the terminal transition (`print_jobs.tray_snapshot`) and auto-assigns RFID spools whenever the tray signature changes. `archive_job` calls `spool_inventory.record_job_usage` before its commit and prices filament from the usage rows. `services/print_plan.py` builds a plan from (file, plate, printer, overrides). It backs `/print/plan`, the reprint route and the dialog. `/print/send` and the legacy `/printers/{id}/print` share `print_plan.create_job`.

**Tech Stack:** FastAPI + SQLAlchemy 2 async/sync, Alembic, Celery, Redis, pytest + testcontainers; React 19, TanStack Query/Router, shadcn/ui, Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-21-bambuddy-parity-design.md` §P5. Also §Integration rule: spools → `inventory:write`, slot assignment → `printers:control`, plan/send → `printers:print`. P6 hooks: the Profile step offers only "As sliced", and P6's dispatcher calls `print_plan.create_job`, which also backs `/print/send`.

**Branch:** `p5-spools` from `main` (after P4 merges). Leave `compose.yaml` alone. Agents never `git stash`/`checkout`.

## Global Constraints

- Commit: `git -c user.name=metril -c user.email=1517921+metril@users.noreply.github.com commit -m "<type(scope): subject>"`. No trailers, no AI mention.
- **Test discipline:** run only the task's targeted files; the controller runs the full suite. Backend: `cd backend && timeout 600 uv run pytest tests/<file> -q`, `uv run ruff check . && uv run ruff format --check .`. Web: `cd web && npx vitest run <file>`, `npm run build` (`tsc --noEmit` is a no-op), `npx oxlint` (no new warnings vs baseline).
- Migration: one revision, `e6b1d4a8c2f7_p5_spools`, `down_revision = "142d0b67c477"`.
- Slot addressing: `spool_slots.slot` is the in-unit tray index `0..3`. The external spool is `ams_unit=255, slot=254`. **Global tray id** (the value in `ams_mapping` and in `est_filament[].tray`) is `ams_unit*4 + slot` for AMS trays and `254` for external. An unused slicer filament gets `-1`. Constants live in `spool_inventory`: `EXTERNAL_UNIT=255`, `EXTERNAL_SLOT=254`, `UNMAPPED=-1`, `def global_tray(unit, slot) -> int`, `def split_tray(g) -> tuple[int, int]`.
- RFID: `tray_uuid` values that are empty or all `"0"` (`"0"*32`) count as no RFID (`None`). `remain` < 0 or missing → `remain_pct=None`.
- `ams_mapping` length = max slicer filament id on the plate. Index = `filament_id - 1` (1-based `id` attribute in `slice_info.config`). If `id` is absent (old blob meta), use the enumerate index.
- Matching (`ams_match`): type equality after `.strip().upper()`. Among same-type trays, pick the smallest ΔE_OK (Euclidean distance in OKLab, components 0..1). `DE_OK = 0.10`. Status `ok` (type match, ΔE ≤ DE_OK) / `color` (type match, ΔE > DE_OK, amber) / `type` (no same-type tray, tray None, red) / `none` (printer reports no trays, red). An override always wins and is re-graded with the same rules. Trays may be shared by several filaments.
- Usage: `source` ∈ `3mf|ams_delta|manual`. 3MF grams per row = `used_g × ratio`. `ratio = 1` for completed runs, otherwise the same layer/progress ratio as `costing.charged_grams`. Fallback `ams_delta` applies only when no row has a resolvable tray. It covers trays with `start.remain_pct - end.remain_pct > 0`, grams = `delta/100 × spool.label_weight_g`. **Idempotent per job:** delete the job's existing `3mf`/`ams_delta` rows and subtract their grams from `weight_used_g` before rewriting. `manual` rows are never touched. Rows with grams ≤ 0 are skipped. `last_used_at = job.finished_at or now`.
- Remaining = `label_weight_g - weight_used_g` (may go negative; the UI clamps to 0). Low stock = `remaining_g < low_stock_g`. `low_stock_g` is NOT NULL with `server_default 100`. `label_weight_g` server_default 1000. `weight_used_g` server_default 0.
- Cost: filament cost = Σ `grams/1000 × (spool.cost_per_kg ?? AppConfig.filament_cost_per_kg)`. Grams with no spool use the global rate. Stats compute usage-row cost at query time with the same coalesce. `spool_usage` has no cost column (matches the spec).
- No `spoolman_id` anywhere. The service file is `spool_inventory.py`; `services/spool.py` is upload staging and must not be touched.
- Web query keys: `["spools", filters]`, `["spool", id]`, `["print-plan", body]`. Slot changes invalidate `["spools"]` and `["printers"]`.
- Visual tasks (9–12) invoke the `frontend-design` skill first. Keep the P3 density tokens: 13px body, 28px rows, `tabular-nums`, amber only for primary/in-progress, and low stock is a muted badge (not red).

## Review Focus

1. Usage decrement is idempotent across archive redelivery and the 3MF→ams_delta switch, and manual rows survive. Task 3 `test_record_usage_idempotent`, `test_redelivery_replaces_ams_delta_with_3mf`, Task 5 `test_archive_twice_decrements_once`.
2. AMS-delta fallback only fires when no 3MF tray is resolvable. Task 3 `test_ams_delta_fallback`.
3. Mapping semantics (index = filament_id−1, global tray, 254 external, −1 unused) are consistent across plan, send and archive. Task 2 `test_match_table`, Task 7 `test_plan_mapping_indices`, Task 5 `test_archive_tray_from_filament_id`.
4. RFID auto-assign moves a spool between slots and never steals a manual assignment without a uuid. Task 4 `test_rfid_auto_assign_moves_spool`.
5. Permission and ACL: spools need `inventory:write`, slots need per-printer control, plan/send need per-printer print, and reprint enforces the archive's printer ACL. Task 6 `test_slot_requires_control`, Task 7 `test_send_requires_printer_print`, plus updated `test_perm_sweep.py`/`test_auth_sweep.py`.

---

### Task 1: Schema: spools, spool_slots, spool_usage, tray snapshot

**Files:** Create `backend/app/models/inventory.py`, `backend/alembic/versions/e6b1d4a8c2f7_p5_spools.py`, `backend/tests/test_migrations_p5.py`. Modify `backend/app/models/__init__.py`, `backend/app/models/enums.py`, `backend/app/models/printing.py`.

**Interfaces:**
```python
class SpoolUsageSource(StrEnum): THREE_MF = "3mf"; AMS_DELTA = "ams_delta"; MANUAL = "manual"
class Spool(Base):  # "spools"
    id; material_id FK materials SET NULL nullable index; material_type str NOT NULL; brand str?;
    color_hex str? ("#RRGGBB"); color_name str?; label_weight_g float NOT NULL default 1000;
    empty_weight_g float?; weight_used_g float NOT NULL default 0; cost_per_kg float?;
    location str?; low_stock_g float NOT NULL default 100; tray_uuid str? UNIQUE;
    archived_at datetime?; last_used_at datetime?; created_by_user_id FK users SET NULL index; created_at
class SpoolSlot(Base):  # "spool_slots"; UniqueConstraint(printer_id, ams_unit, slot)
    id; printer_id FK printers CASCADE; ams_unit int; slot int; spool_id FK spools CASCADE UNIQUE  # one slot per spool
class SpoolUsage(Base):  # "spool_usage"; Index(print_job_id)
    id; spool_id FK spools CASCADE index; print_job_id FK print_jobs SET NULL nullable;
    grams float NOT NULL; source str_enum(SpoolUsageSource,"spool_usage_source"); created_at
PrintJob.tray_snapshot: Mapped[dict | None] = JSONB  # {"start": [tray...], "end": [tray...]}
```

- [ ] Failing test `test_migrations_p5.py`: upgrade → tables, unique `(printer_id, ams_unit, slot)`, unique `tray_uuid`, `print_jobs.tray_snapshot` exist; downgrade -1 drops them and the enum type; upgrade again succeeds (follow `test_migrations_p2.py` helpers).
- [ ] Run, expect fail.
- [ ] Implement models + migration (drop the enum type in downgrade). `test_migration_drift.py` must stay green.
- [ ] Run `tests/test_migrations_p5.py tests/test_migration_drift.py`, expect pass.
- [ ] Commit `feat(inventory): add spools, spool slots and usage tables`.

### Task 2: AMS matcher (pure)

**Files:** Create `backend/app/services/ams_match.py`, `backend/tests/test_ams_match.py`.

**Interfaces:**
```python
@dataclass(frozen=True) class TrayInfo: tray: int; type: str | None; color: str | None  # global id
@dataclass(frozen=True) class FilamentNeed: filament_id: int; type: str | None; color: str | None; grams: float | None
@dataclass(frozen=True) class Match: filament_id: int; tray: int | None; status: Literal["ok","color","type","none"]; delta_e: float | None
def hex_to_oklab(hex_: str) -> tuple[float, float, float]
def delta_e_ok(a: str, b: str) -> float
def match(needs: list[FilamentNeed], trays: list[TrayInfo], overrides: dict[int, int] | None = None) -> list[Match]
def to_ams_mapping(matches: list[Match]) -> list[int]  # len = max filament_id; UNMAPPED fill
```
OKLab body (the only code body in this plan):
```python
def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

def hex_to_oklab(hex_: str) -> tuple[float, float, float]:
    h = hex_.lstrip("#")[:6]  # tolerate 8-hex RRGGBBAA
    r, g, b = (_lin(int(h[i : i + 2], 16) / 255) for i in (0, 2, 4))
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (v ** (1 / 3) for v in (l, m, s))
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )

def delta_e_ok(a: str, b: str) -> float:
    return math.dist(hex_to_oklab(a), hex_to_oklab(b))
```
A missing colour on either side counts as ΔE 0 when the types match (status `ok`).

- [ ] Failing `test_match_table` (parametrized): exact type + same colour → ok; PLA red vs PLA trays {orange, red} → the red tray; PLA vs only PETG → `type`/None; no trays → `none`; type match but ΔE > 0.10 → `color`; `"pla "` vs `"PLA"` matches; `PETG-CF` ≠ `PETG`; override to a PETG tray for a PLA need → status `type`, tray kept; two needs share one tray. Also `test_delta_e_identity_and_order` (white/black ≈ 1.0, identical = 0) and `test_to_ams_mapping_gaps` (ids {1,3} → `[t1, -1, t3]`; external 254 passes through).
- [ ] Run, expect fail. Implement. Run `tests/test_ams_match.py`, expect pass. Ruff.
- [ ] Commit `feat(print): add AMS filament matcher with OKLab colour distance`.

### Task 3: spool_inventory service

**Files:** Create `backend/app/services/spool_inventory.py`, `backend/tests/test_spool_inventory.py`.

**Interfaces (sync Session unless noted):**
```python
EXTERNAL_UNIT = 255; EXTERNAL_SLOT = 254; UNMAPPED = -1
def global_tray(unit: int, slot: int) -> int; def split_tray(g: int) -> tuple[int, int]
def remaining_g(s: Spool) -> float; def is_low_stock(s: Spool) -> bool
def rate_per_kg(s: Spool | None, global_per_kg: float) -> float
def assign_slot(session, printer_id, unit, slot, spool_id: int | None) -> None  # None clears; moving a spool frees its old slot
def auto_assign_from_trays(session, printer_id, trays: list[dict]) -> bool  # True if changed
def spool_for_tray(session, printer_id, tray: int) -> Spool | None
def record_job_usage(session, job: PrintJob, est_filament: list[dict], result: ArchiveResult) -> list[tuple[Spool | None, float]]
async def slot_map(db: AsyncSession, printer_ids: Iterable[int]) -> dict[tuple[int, int], Spool]  # (printer_id, global tray)
```
`record_job_usage` returns (spool or None, grams) for every charged row, including spool-less ones, so the caller can price the run. It never commits.

- [ ] Failing tests: `test_remaining_and_low_stock` (label 1000, used 850 → 150 g, not low at 100; used 950 → 50 g, low); `test_rate_fallback`; `test_assign_slot_moves_spool`; `test_record_usage_decrements` (two rows on trays 0 and 1 → two `3mf` rows, `weight_used_g` up, `last_used_at` set); `test_record_usage_partial_ratio` (failed at layer 50/100 → half); `test_record_usage_idempotent` (call twice → one set of rows, single decrement; a pre-existing `manual` row untouched); `test_ams_delta_fallback` (est rows lack `tray`, snapshot start 80% → end 70% on tray 2, label 1000 → 100 g `ams_delta`); `test_redelivery_replaces_ams_delta_with_3mf`; `test_unassigned_tray_returns_spoolless_grams`. Use `db_session`/sync session fixtures as `test_archive_service.py` does.
- [ ] Run, expect fail. Implement. Run `tests/test_spool_inventory.py`, expect pass.
- [ ] Commit `feat(inventory): add spool inventory service with idempotent usage`.

### Task 4: Adapter trays, slicer filament ids, printerd auto-assign + tray snapshot

**Files:** Modify `backend/app/printers/bambu.py` (`_ams_trays`), `backend/app/printers/fake.py`, `backend/app/printers/base.py` (tray docstring), `backend/app/schemas/printers.py` (`AmsTrayOut`), `backend/app/pipeline/slicedmeta.py` (`_parse_slice_info` adds `"filament_id": _parse_int(el.get("id"))`), `backend/app/printerd.py`, `web/src/api/types.ts` (`AmsTray`). Tests: `backend/tests/test_bambu_adapter.py`, `backend/tests/test_printerd.py`, `backend/tests/test_pipeline_metadata.py`.

**Interfaces:** tray dict = `{"slot": global_tray, "ams_unit": int, "tray": int, "color": "#RRGGBB"|None, "material": str|None, "tray_type": str|None, "tray_uuid": str|None, "remain_pct": int|None}`. Unit id comes from `unit["id"]` (falls back to its position) and tray id from `tray["id"]` (falls back to its position). External = `print.vt_tray` with `ams_unit=255, tray=254, slot=254`. `slot` keeps its old meaning (global id), so existing chips are unchanged. `AmsTrayOut` gains the same optional fields plus `spool: SpoolBriefOut | None = None` (filled in Task 6). In printerd: `PrinterWorker._tray_sig` (tuple of `(slot, tray_uuid, tray_type)`); on change → `auto_assign_from_trays` in a `base.sync_session()`, guarded by `contextlib.suppress(Exception)` + log. On creating or first `PRINTING` of a job → `job.tray_snapshot = {"start": trays}`; on terminal → add `"end": trays`.

- [ ] Failing tests: `test_ams_trays_unit_and_rfid` (2 units with ids 0/1, tray ids, `remain` 63, all-zero uuid → None, `remain` −1 → None, vt_tray → slot 254); `test_slice_info_filament_id`; `test_rfid_auto_assign_moves_spool` (spool with uuid U assigned to printer 1 unit 0 slot 2; report U at unit 0 slot 0 → moved; a manually assigned spool without uuid in another slot is untouched; an empty tray clears its slot); `test_tray_snapshot_start_end`; `test_auto_assign_only_on_signature_change` (two identical reports → one DB call).
- [ ] Run, expect fail. Implement. Run `tests/test_bambu_adapter.py tests/test_printerd.py tests/test_pipeline_metadata.py`, expect pass.
- [ ] Commit `feat(printers): report AMS units, RFID and remain, auto-assign RFID spools`.

### Task 5: Archive writes spool_usage + spool-priced cost

**Files:** Modify `backend/app/services/archive.py`, `backend/app/services/costing.py`. Tests: `backend/tests/test_archive_service.py`, `backend/tests/test_costing.py`; extend `backend/tests/archive_fixtures.py` (`sliced_3mf_bytes` filaments get `id` attrs; a `spool_on_tray` helper).

**Interfaces:**
- est rows gain `"filament_id"`. `tray = mapping[filament_id-1]` if the id is present and in range, else `mapping[i]`. Skip `tray` when the value is `-1`.
- `price_run(filament_g, duration_s, rates, *, energy_cost=None, filament_cost: float | None = None)`: when `filament_cost` is given it replaces `filament_g/1000 × rate`.
- `costing.filament_cost(usages: list[tuple[Spool | None, float]], global_per_kg: float) -> float | None`.
- In `archive_job`, after `arc.est_filament = est` and before pricing: `usages = spool_inventory.record_job_usage(session, job, est, result)`, then `price_run(..., filament_cost=costing.filament_cost(usages, cfg.filament_cost_per_kg))`. Load the config once. The single existing `session.commit()` covers usage too.

- [ ] Failing tests: `test_archive_writes_usage_per_tray`; `test_archive_tray_from_filament_id` (mapping `[3, -1, 0]`, plate uses ids 1 and 3 → trays 3 and 0); `test_archive_twice_decrements_once`; `test_archive_cost_uses_spool_rate` (spool 40/kg on tray 0, 50 g → filament_cost 2.0; a spool-less tray uses the global 20/kg); `test_archive_missing_3mf_uses_ams_delta`; `test_price_run_filament_cost_override`.
- [ ] Run, expect fail. Implement. Run `tests/test_archive_service.py tests/test_costing.py tests/test_archive_task.py`, expect pass.
- [ ] Commit `feat(archive): record spool usage per tray and price filament by spool`.

### Task 6: Spools API + slot assignment + tray enrichment

**Files:** Create `backend/app/api/spools.py`, `backend/app/schemas/inventory.py`, `backend/tests/test_spools_api.py`. Modify `backend/app/api/__init__.py` (mount on `protected_router`), `backend/app/api/printers.py` (slots route; status + overview fill `AmsTrayOut.spool` via `slot_map`), `backend/app/services/printer_overview.py`, `backend/tests/test_auth_sweep.py` (`spool_id: "1"`, `unit: "0"`, `slot: "0"`), `backend/tests/test_perm_sweep.py` (only if a GET needs listing; no mutating route is allowlisted).

**Interfaces:**
```
GET    /api/spools?archived=false&low_stock=&material_type=&q=   -> list[SpoolOut]   (any session)
GET    /api/spools/{spool_id}                                    -> SpoolDetailOut   (usage[], series[])
POST   /api/spools                     inventory:write          -> 201 SpoolOut (created_by_user_id = ctx.user.id)
PATCH  /api/spools/{spool_id}          inventory:write          -> SpoolOut (weight_used_g editable; archived: bool toggles archived_at)
DELETE /api/spools/{spool_id}          inventory:write          -> 204 (owner-or-admin via require_owner_or_admin)
POST   /api/spools/{spool_id}/usage    inventory:write          -> 201 {grams>0} manual row, decrements
PUT    /api/printers/{printer_id}/slots/{unit}/{slot}  require_printer_access("control") body {spool_id: int|null} -> list[AmsTrayOut]
SpoolOut: all columns + remaining_g, low_stock, slot: {printer_id, ams_unit, slot}|None
SpoolDetailOut(SpoolOut): usage: [{id, print_job_id, archive_id|None, model_name|None, grams, source, created_at}],
                          series: [{t, remaining_g}]  # oldest→newest, starts at label_weight_g
SpoolBriefOut: {id, material_type, brand, color_hex, color_name, remaining_g, low_stock}
```
Validation: `color_hex` `^#[0-9A-Fa-f]{6}$`; weights ≥ 0; `unit` ∈ 0..3 or 255; `slot` ∈ 0..3 or (unit 255 and slot 254) → else 422. A duplicate `tray_uuid` → 409.

- [ ] Failing tests: CRUD round-trip; `test_low_stock_filter`; `test_viewer_cannot_write_spool` (403); `test_slot_requires_control` (member with print-only ACL → 403; control → 200, the spool's slot is set); `test_slot_validation`; `test_status_trays_carry_spool`; `test_spool_detail_usage_and_series`; `test_manual_usage`.
- [ ] Run, expect fail. Implement. Run `tests/test_spools_api.py tests/test_auth_sweep.py tests/test_perm_sweep.py tests/test_printer_status_api.py tests/test_printer_overview_api.py`, expect pass.
- [ ] Commit `feat(api): add spools CRUD, usage and AMS slot assignment`.

### Task 7: Print plan / send / reprint

**Files:** Create `backend/app/services/print_plan.py`, `backend/app/api/print_flow.py` (router prefix `/print`, gated by `require_printer_enabled`), `backend/app/schemas/print_flow.py`, `backend/tests/test_print_plan_api.py`. Modify `backend/app/api/printers.py` (`start_print` delegates to `print_plan.create_job`), `backend/app/api/archives.py` (reprint), `backend/app/api/__init__.py`, `backend/tests/test_archives_api.py` (replace the 501 test).

**Interfaces:**
```python
class PlanIn(BaseModel): file_id: int; plate: int | None = None; printer_id: int | None = None; overrides: dict[int, int] = {}
class PlanFilament(BaseModel): filament_id; type; color; grams; tray: int | None; status; delta_e; spool: SpoolBriefOut | None
class PlanPrinter(BaseModel): id; name; model; idle: bool; compatible: bool; reason: str | None
class PlanOut(BaseModel): file_id; model_id; plate; plates: list[PlateOut]; printer_id: int | None; printers: list[PlanPrinter]
    filaments: list[PlanFilament]; ams_mapping: list[int]; use_ams: bool; est_duration_s; est_grams; est_cost
    warnings: list[str]; options: {bed_levelling: True, flow_cali: True, timelapse: False}
class SendIn(PrintRequest): printer_id: int
async def build_plan(db, settings, redis, ctx, body: PlanIn) -> PlanOut
async def create_job(db, settings, ctx, printer_id: int, req: PrintRequest) -> PrintJob  # moved body of start_print
```
- Plate defaults to the first one in `BlobMeta.raw["plates"]`. A non-`gcode_3mf` file → 422.
- `printers` = printers in `visible_printer_ids(ctx)` where the user can print. `idle = preflight_ok(state)`. `compatible` is false when the file's `printer_model` is set and differs from `Printer.model` (case-insensitive substring), reason `"sliced for <x>"`. Default `printer_id` = the requested one, else the first idle compatible printer.
- Trays come from Redis state + `slot_map`. Each need is `filament_id`/`type`/`color`/`used_g` from the plate. `use_ams = any tray not in (None, 254)`.
- `est_cost` = machine cost + Σ grams × mapped spool rate (global fallback).
- `warnings`: `"printer offline"`, `"printer busy"`, `"<n> filament(s) need a slot"`, `"spool <name> low: <g> g left"` when remaining < the need.
- `POST /print/plan` → `ctx.require_printer(printer_id, "print")` when a printer is given; otherwise the list is just filtered. `POST /print/send` (`SendIn`) → `require_printer(printer_id, "print")` + `create_job` → 201 `PrintJobOut`.
- `POST /archives/{id}/reprint` (`printers:print` + archive printer ACL) → `PlanOut` built with `file_id = job.file_id`, or else the `File` whose `blob_hash == arc.archive_blob`. If neither exists → 409 `"archived file is not in the library"`. `plate = arc.plate`, `printer_id = job.printer_id`, `overrides` come from `est_filament[].tray`.

- [ ] Failing tests: `test_plan_mapping_indices` (fake state with 2 AMS trays + external; plate ids 1, 2 → mapping/statuses); `test_plan_override_regrades`; `test_plan_warns_busy_and_low_spool`; `test_plan_filters_printers_by_acl`; `test_send_requires_printer_print`; `test_send_creates_job_with_mapping` (eager Celery + `fake_adapter`, `PrintSpec.ams_mapping` matches); `test_legacy_print_route_still_works`; `test_reprint_returns_plan`; `test_reprint_409_without_library_file`; `test_reprint_acl`.
- [ ] Run, expect fail. Implement. Run `tests/test_print_plan_api.py tests/test_archives_api.py tests/test_printers_api.py tests/test_send_flow.py tests/test_auth_sweep.py tests/test_perm_sweep.py`, expect pass.
- [ ] Commit `feat(print): add print plan, send and real reprint endpoints`.

### Task 8: Filament stats from spool_usage

**Files:** Modify `backend/app/services/stats.py`, `backend/app/schemas/stats.py`, `backend/app/api/stats.py`. Test `backend/tests/test_stats_api.py`.

**Interfaces:**
- `_material_usage` = spool_usage grouped by `coalesce(Material.name, Spool.material_type)` (with `material_id` when linked) ∪ `prints` with `source=manual` (existing material/free-text logic, manual only, so auto runs are not double counted). Same names are summed.
- `GET /api/stats/filament?days=90&bucket=week|day|month` → `FilamentStatsOut{by_material: [FilamentSeries], by_spool: [FilamentSeries]}`, where `FilamentSeries{key, name, color_hex|None, total_g, total_cost, points: [{t, grams, cost}]}` uses `date_trunc(bucket, spool_usage.created_at)`. Cost = `grams/1000 × coalesce(spool.cost_per_kg, global)`. Not cached; validate `days` 1..730.

- [ ] Failing tests: `test_material_usage_from_spool_usage` (auto run counted once via usage, manual print counted, same material merged); `test_filament_stats_per_material_and_spool` (2 spools, 3 usage rows across 2 weeks → buckets, cost with one spool lacking price → global rate); `test_filament_stats_bucket_validation`.
- [ ] Run, expect fail. Implement. Run `tests/test_stats_api.py`, expect pass.
- [ ] Commit `feat(stats): per-material and per-spool filament usage over time`.

### Task 9: Web Spools page

**Files:** Create `web/src/api/spools.ts`, `web/src/pages/SpoolsPage.tsx`, `web/src/components/inventory/{SpoolTable,SpoolForm,SpoolUsageHistory}.tsx` + `.test.tsx` for each and the page. Modify `web/src/api/types.ts` (`SpoolOut`, `SpoolDetailOut`, `SpoolBrief`, `AmsTray` additions), `web/src/routes.tsx` (`/inventory/spools` → `SpoolsPage`; accept `?low=1`).

**Interfaces:** `useSpools(filters)`, `useSpool(id)`, `useCreateSpool`, `usePatchSpool`, `useDeleteSpool`, `useAddUsage`, `useAssignSlot(printerId)`.
- `SpoolTable`: swatch, material/brand/colour name, a remaining-g bar (`tabular-nums`, clamp 0), a low-stock muted badge, location, slot ("A1 Mini · AMS 1-3"/"Ext"), last used, and row → a detail `Sheet`. Filters: search, material type, "Low stock", "Show archived".
- `SpoolForm` (create/edit dialog): material select (from `useMaterials`, optional, prefills type/colour), type, brand, colour picker + name, label/empty weight, used grams, plus a "Weigh spool" helper: gross g → `used = label − (gross − empty)` when `empty_weight_g` is set. Also cost/kg, location, low-stock g, tray uuid (read-only when it came from RFID). Write controls are hidden without `can("inventory:write")`.
- `SpoolUsageHistory`: `Sparkline` of `series` + a table (run link to History `RunDetailSheet` when `archive_id`, date, grams, source chip), and an "Add manual usage" form.
- Materials stay on `/inventory/materials` (`MaterialsPage` already hosts `MaterialsSection`; P3 removed it from Settings). Add a "Materials" link in the SpoolsPage header for the fold-in.

- [ ] Invoke `frontend-design`. Failing vitest: table renders remaining + low badge, filter `low` narrows, form computes used grams from gross weight, viewer sees no Add button, usage history renders the sparkline + rows.
- [ ] Run `npx vitest run src/components/inventory src/pages/SpoolsPage.test.tsx`, expect fail. Implement. Pass. `npm run build`, `npx oxlint`.
- [ ] Commit `feat(web): spool inventory page with usage history`.

### Task 10: SlotAssignPopover + dashboard low-stock

**Files:** Create `web/src/components/inventory/SlotAssignPopover.tsx` + test. Modify `web/src/components/ui/filament-chip.tsx` (optional `lowStock` muted dot/badge, `remainPct` tick), `web/src/components/print/PrinterCard.tsx` + `web/src/pages/PrinterDetailPage.tsx` (chips wrapped in the popover when `canPrinter(id,"control")`, plain chips otherwise), tests `PrinterCard.test.tsx`, `filament-chip.test.tsx`.

**Interfaces:** `<SlotAssignPopover printerId tray={AmsTray}>{chip}</SlotAssignPopover>`. It shows the tray's type/colour/remain% and its RFID uuid; a spool combobox (non-archived, same type first, sorted by ΔE using a small client `oklab` helper in `lib/color.ts`); "Clear"; and "New spool from this tray", which opens `SpoolForm` prefilled with type/colour/tray_uuid and assigns on save. Mutation → `PUT /printers/{id}/slots/{ams_unit}/{tray}`.

- [ ] Invoke `frontend-design`. Failing vitest: a low-stock tray chip shows the badge; control users get a popover trigger and others don't; picking a spool calls PUT with `ams_unit/tray`; "Clear" sends `spool_id: null`; external tray uses `255/254`.
- [ ] Run, fail, implement, pass. Build + oxlint.
- [ ] Commit `feat(web): assign spools to AMS slots from printer cards`.

### Task 11: PrintDialog + launchers

**Files:** Create `web/src/components/print/PrintDialog.tsx`, `web/src/components/print/PrintDialog.test.tsx`, `web/src/api/printFlow.ts` (`usePrintPlan(body)`, `useSendPrint()`, `useReprint(archiveId)`). Modify `web/src/components/model-detail/SendToPrinterButton.tsx` (button opens `PrintDialog`; delete `SendToPrinterDialog`), `web/src/components/model-detail/ModelHeader.tsx` (a "Print…" button when the model has a sliced file and the user has `printers:print`), `web/src/pages/QueuePage.tsx`, `web/src/components/print/UpNextList.tsx` ("Print…" → `PrintDialog` with `modelId`), `web/src/components/print/RunDetailSheet.tsx` ("Reprint" when `can("printers:print")`), `web/src/components/shell/CommandPalette.tsx` ("Print…" action → `PrintDialog` at the Source step), and the tests of the touched components (`RunDetailSheet.test.tsx` now expects Reprint).

**Interfaces:** `<PrintDialog open onOpenChange source={{kind:"file", fileId}|{kind:"model", modelId}|{kind:"archive", archiveId}|{kind:"pick"}} onSent?/>`.
- Left step rail: 1 Source · 2 Plate · 3 Profile · 4 Filament · 5 Send. A sticky footer shows est time / grams / cost from the current plan, plus Back/Next.
- **Source**: shown for `pick`, or for `model` with more than one sliced file. It is a model search (`GET /models?q=`) → that model's `gcode_3mf` files. Skipped otherwise.
- **Plate**: a thumb grid (`/api/blobs/{hash}/plates/{n}/thumb`, caption from `PlatePanel`'s helper). Auto-skipped for a single plate but still reachable from the rail.
- **Profile**: one selected radio "As sliced" (P6 adds recipes).
- **Filament**: one row per need: swatch, type, grams → tray chip + spool remaining g. Status styling: ok plain, `color` amber, `type`/`none` red. Each row has an override `Select` of the printer's trays, which re-fetches the plan with `overrides`.
- **Send**: printer radio list from `plan.printers` (disabled when not idle/compatible, with the reason), toggles for bed levelling / flow cali / timelapse, then "Send now" (`POST /print/send`, disabled while any status is red unless every red row has been overridden) and "Add to queue" (`useEnqueueModel` → `POST /queue {model_id}`, shown when `model_id` is known; the plan options are not persisted until P6). Warnings appear above the buttons.
- `archive` source calls `/archives/{id}/reprint` for the initial plan, then `/print/plan` for changes.

- [ ] Invoke `frontend-design`. Failing vitest (`PrintDialog.test.tsx`, msw/fetch mocks as existing tests do): `file` source starts at Plate (single plate → Filament); rail click navigates back; an override select re-posts the plan with `overrides {1: 4}` and the row turns from red to plain; Send is disabled with an unresolved red row; Send posts `ams_mapping`/toggles/printer and closes + toasts; Add to queue posts the model id; `archive` source hits reprint. Also: UpNextList/RunDetailSheet/CommandPalette open the dialog.
- [ ] Run, fail, implement, pass (`npx vitest run src/components/print src/components/model-detail src/components/shell src/pages/QueuePage.test.tsx`). Build + oxlint.
- [ ] Commit `feat(web): one-modal print dialog with filament mapping`.

### Task 12: History filament stats

**Files:** Modify `web/src/components/print/HistoryStats.tsx` (+ test), `web/src/api/stats.ts`, `web/src/api/types.ts`.

**Interfaces:** `useFilamentStats({days, bucket})`. The section has a "By material | By spool" segmented toggle and a 30/90/365-day range. Each series row shows a swatch, name, total g, total cost and a `Sparkline` of grams per bucket. Spool rows link to `/inventory/spools?spool=<id>`.

- [ ] Invoke `frontend-design` (follow the `dataviz` skill for series colours). Failing vitest: renders material rows with totals, toggling shows spools, the range change refetches with `days`.
- [ ] Run, fail, implement, pass. Build + oxlint.
- [ ] Commit `feat(web): per-material and per-spool filament usage in history`.

### Task 13: Docs, live-verify checklist, gates

**Files:** Create `docs/superpowers/plans/2026-10-22-p5-live-verify.md`. Modify `docs/` printer/feature docs where `SendToPrinterDialog`, the reprint stub or `prints.filament` stats are described (grep), and `README`'s feature list if it covers printing.

- [ ] Live-verify checklist (A1 Mini + AMS lite): (1) `tray_uuid` for a Bambu RFID spool vs a third-party spool (expect `"0"*32`) and whether `remain` is −1 for non-RFID; (2) `ams_mapping` index semantics: send a 2-colour plate whose project has an unused filament 2 and confirm the `filament_id-1` index plus `-1` for unused; (3) the external-spool mapping value (254 vs 255) and the `vt_tray` id; (4) AMS unit `id` on a single AMS lite; (5) remain% granularity during a print (is the ams_delta fallback meaningful?); (6) drill: RFID spool auto-assigns, two-colour print via the modal, `weight_used_g` drops by the 3MF grams, run cost uses the spool price.
- [ ] Gates (controller): `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`; `cd web && npm run build && npx oxlint && npx vitest run`.
- [ ] Commit `docs(print): add P5 live-verify checklist`.
