# P6 Bambu Studio Slicer, Profiles, Recipes and Dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (or superpowers:executing-plans) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Bambu Studio CLI sidecar slices mesh/project files using imported `system/BBL` profiles bundled into recipes. The print queue becomes a real multi-printer dispatcher: per-file, per-plate, per-printer items move through `waiting → slicing → ready → dispatched` (or `failed`/`held`). A 10 s beat tick sends each one to an idle, compatible printer that its creator may still print on. Single, spread and first-n fanout are supported, and failures retry up to 3 times.

**Architecture:** `services/slicer_client.py` defines OUR contract (`SlicerClient`). A one-file adapter maps it onto the pinned `ghcr.io/maziggy/bambu-studio-api` HTTP API. `services/slicer_profiles.py` imports a BBL bundle and resolves `inherits:` chains into flat `slicer_profiles.data`. `tasks/slicing.py::slice_file` (cpu queue) calls the sidecar and stores the output `.gcode.3mf` as a new File on the source file's revision. It parses that file with `slicedmeta` and marks the queue item `ready`. `services/dispatcher.py` holds the pure parts (`select_printers`, `compatibility_reason`) and the DB parts (`eligible_queue_items`, `dispatch_once`). The dispatcher sends through `print_plan.create_job_sync`, the sync twin of P5's `create_job`, and both share one validator. Beat `dispatch_queue` is a new additive entry.

**Tech Stack:** FastAPI + SQLAlchemy 2 async/sync, Alembic, Celery + beat, Redis, httpx + respx, pytest + testcontainers; React 19, TanStack Query/Router, shadcn/ui, Vitest + Testing Library; Docker Compose `include:`.

**Spec:** `docs/superpowers/specs/2026-10-21-bambuddy-parity-design.md` §P6. Also §Integration rule: profiles/recipes → `inventory:write`; queue/dispatch/retry → `printers:print` + per-printer ACL; `created_by_user_id` on every user-created row; the dispatcher re-checks `can_print`. P7 hook: P7 adds `review_state`/`hold_reason` to `print_queue` and extends ONLY `dispatcher.eligible_queue_items`. P8 hook: `dispatcher.printer_hold_reason(client, printer_id) -> str | None`, which returns `None` in P6 and is read from `ha:alert:{printer}` in P8.

**Branch:** `p6-slicer` from `main` after P5 merges. The user has an uncommitted `compose.yaml` hunk (api port `9080:8080`) that must never be committed (Task 2 shows the exact staging). Agents never `git stash`/`checkout`.

## Global Constraints

- Commit: `git -c user.name=metril -c user.email=1517921+metril@users.noreply.github.com commit -m "<type(scope): subject>"`. No trailers, no AI mention.
- **Test discipline:** run only the task's targeted files; the controller runs the full suite. Backend: `cd backend && timeout 600 uv run pytest tests/<file> -q` and `uv run ruff check . && uv run ruff format --check .`. Web: `cd web && npx vitest run <file>`, `npm run build` (`tsc --noEmit` is a no-op), `npx oxlint` (no new warnings vs baseline).
- Migration: one revision, `b4d7e2a9c6f1_p6_slicer`, `down_revision = "e6b1d4a8c2f7"`.
- Enums (`models/enums.py`): `ProfileKind = printer|process|filament`, `ProfileSource = import|builtin`, `Fanout = single|spread|first_n`, `QueueState = waiting|slicing|ready|dispatched|failed|held`.
- Constants (`services/dispatcher.py`): `QUEUE_MAX_ATTEMPTS = 3`, `DISPATCH_INTERVAL_S = 10`, `DISPATCH_LOCK_KEY = "tdmm:queue:dispatch:lock"`, `DISPATCH_IDLE_STATES = {"IDLE", "FINISH"}`. `FAILED` does NOT count as idle for auto-dispatch, because a failed plate needs a human; `preflight_ok` is unchanged for manual send. `SLICES_PER_TICK = 2`. `NOZZLE_TOL_MM = 0.01`.
- Sidecar: compose service `slicer`, profile `slicer`, image `ghcr.io/maziggy/bambu-studio-api:${SLICER_TAG:-<pinned>}`. `<pinned>` is the newest non-`latest` tag at implementation time; record it in the compose comment and in the live-verify doc. Port 3001, health `GET /health`. Env `TDMM_SLICER_URL` (default `None`; compose sets `http://slicer:3001`). AppConfig field `slicer_url: str | None` overlays env like the other fields. Client timeout: connect 5 s, slice 600 s.
- `SLICEABLE_FORMATS = {BlobFormat.THREEMF, BlobFormat.STL, BlobFormat.STEP, BlobFormat.OBJ}`.
- Profiles: `(kind, name)` is UNIQUE, and re-import upserts on it. Only BBL presets with `instantiation == "true"` become rows; abstract parents are used only to resolve `inherits`. BBL dirs are `machine/`→`printer`, `process/`→`process`, `filament/`→`filament`.
- Queue item rules: an item with `file_id IS NULL` is a bare worklist entry (the legacy per-model entry) and is never dispatched. A partial unique index `uq_print_queue_bare_model ON print_queue(model_id) WHERE file_id IS NULL` replaces the old `model_id UNIQUE`. As-sliced items are created `ready`. Recipe items are created `waiting`. `copies > 1` is split on dispatch: the sent row becomes `copies=1, dispatched`, and a clone with `copies-1` keeps the same position. Under `single` fanout the clone is pinned to the assigned printer via `printer_id`.
- Fanout source: `recipe.fanout/n` if the item has a recipe, otherwise `single`. A set item `printer_id` (or `recipe.target_printer_id`) restricts candidates to that printer. `recipe.target_printer_model` filters candidates by a case-insensitive substring on `Printer.model`.
- Retry: a slice error, a `create_job_sync` error, or a dispatched job that ends `FAILED` with `started_at IS NULL` each increment `attempts`. If `attempts < 3`, the item goes back to `waiting` (no sliced file yet) or `ready`. At 3 it becomes `failed`. Each case sets `last_error` and clears `assigned_printer_id`/`print_job_id`. `POST /queue/{id}/retry` accepts only `failed|held` (otherwise 409) and resets `attempts=0, last_error=None`.
- Revoked access: printers the creator cannot `can_print` on are removed from the candidates. If no permitted candidate remains (or the creator is NULL or inactive), the item becomes `held` with `last_error="creator cannot print on any target printer"`.
- Queue visibility: `GET /queue` returns items whose `printer_id` is NULL or in `visible_printer_ids(ctx)`. By default it omits `dispatched` items whose job is terminal; `?include_done=true` includes them.
- Web query keys: `["profiles", kind]`, `["recipes"]`, `["slicer-health"]`, `["queue"]`. Queue mutations also invalidate `["printers", "overview"]`.
- Visual tasks (9–11) invoke the `frontend-design` skill first. Keep the P3 density tokens: 13px body, 28px rows, `tabular-nums`, and amber only for primary/in-progress. State chips: waiting=muted, slicing/ready=amber outline, dispatched=muted solid, failed=red, held=muted with a lock icon.

## Review Focus

1. Fanout picks the right printers for every mode and printer count, and copies split without double-sending. Task 7 `test_select_printers_matrix`, `test_copies_split_single_pins_printer`, `test_one_item_per_printer_per_tick`.
2. Nothing dispatches to a printer whose creator lost `can_print`, nothing dispatches to an incompatible printer, and nothing dispatches while held. Task 7 `test_revoked_access_holds`, `test_compat_rejects_model_nozzle_material`, `test_held_not_dispatched`.
3. Retry is bounded at 3 across slice and send failures, and manual retry only applies from failed/held. Task 5 `test_slice_failure_retries_then_fails`, Task 7 `test_send_failure_retry`, Task 6 `test_retry_endpoint_states`.
4. `inherits` chains resolve parent→child with cycle/missing-parent errors, and only instantiable presets import. Task 3 `test_resolve_inherits_chain`, `test_import_bundle_upserts`.
5. The sidecar contract survives an adapter swap, and `/health` reports the version. Task 2 `test_slice_contract`, `test_health_contract`. All new mutating routes are gated (`test_perm_sweep.py` stays green with no allowlist additions).

---

### Task 1: Schema: slicer_profiles, print_recipes, print_queue columns

**Files:** Create `backend/app/models/slicing.py`, `backend/alembic/versions/b4d7e2a9c6f1_p6_slicer.py`, `backend/tests/test_migrations_p6.py`. Modify `backend/app/models/{__init__,enums,library}.py`.

**Interfaces:**
```python
class SlicerProfile(Base):  # "slicer_profiles"; UniqueConstraint(kind, name)
    id; name str; kind str_enum(ProfileKind); printer_model str?; nozzle_mm float?;
    data JSONB NOT NULL; source str_enum(ProfileSource) default import;
    created_by_user_id FK users SET NULL index; created_at; updated_at
class PrintRecipe(Base):  # "print_recipes"; name UNIQUE
    id; name; printer_profile_id FK slicer_profiles RESTRICT; process_profile_id FK RESTRICT;
    filament_profile_ids JSONB list[int|None] (index = slot/filament_id-1); bed_type str;
    options JSONB {auto_orient, arrange, bed_levelling, flow_cali, timelapse};
    target_printer_id FK printers SET NULL?; target_printer_model str?;
    fanout str_enum(Fanout) default single; n int?; created_by_user_id; created_at
PrintQueueEntry (+): printer_id FK printers SET NULL?; recipe_id FK print_recipes SET NULL?;
    file_id FK files CASCADE?; sliced_file_id FK files SET NULL?; plate int?; copies int default 1;
    state str_enum(QueueState) default waiting index; assigned_printer_id FK printers SET NULL?;
    print_job_id FK print_jobs SET NULL?; attempts int default 0; last_error text?;
    ams_mapping JSONB?; print_options JSONB?   # model_id UNIQUE -> partial unique (file_id IS NULL)
```
- [ ] Write `test_migrations_p6.py`: upgrade adds the tables and columns; an existing queue row survives as `state='waiting', file_id NULL`; two rows with the same `model_id` are allowed when `file_id` is set, and two bare rows are rejected. Downgrade restores `model_id UNIQUE` after deleting non-bare rows. Add a `SlicerProfile` `(kind, name)` uniqueness check.
- [ ] Run `timeout 600 uv run pytest tests/test_migrations_p6.py tests/test_migration_drift.py -q`. Expect FAIL.
- [ ] Implement the models and migration (server defaults: `copies 1`, `attempts 0`, `state 'waiting'`, `fanout 'single'`, `source 'import'`).
- [ ] Rerun, plus `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`. Expect PASS.
- [ ] Commit `feat(db): slicer profiles, recipes and dispatch queue columns`.

### Task 2: Slicer client contract + compose service + settings

**Files:** Create `backend/app/services/slicer_client.py`, `backend/tests/test_slicer_client.py`, `compose.slicer.yaml`. Modify `backend/app/config.py`, `backend/app/services/app_config.py`, `compose.yaml` (one `include:` block only), `.env.example`.

**Interfaces:**
```python
class SlicerError(Exception): ...
class SlicerUnavailable(SlicerError): ...
@dataclass(frozen=True) class SliceOptions: auto_orient: bool = False; arrange: bool = False; plate: int | None = None
class SlicerClient:
    def __init__(self, base_url: str, *, http: httpx.Client | None = None): ...
    def health(self) -> dict  # {"version": str, "ok": bool}
    def profiles(self) -> list[dict] | None  # image's bundled presets if exposed, else None
    def slice(self, model: bytes | Path, filename: str, printer_profile: dict, process_profile: dict,
              filament_profiles: list[dict], bed_type: str, options: SliceOptions) -> bytes  # .gcode.3mf
# Private mapping layer, the ONLY image-specific code: _ENDPOINTS = {"health": ..., "slice": ..., "profiles": ...}
# and _build_slice_request(...) / _parse_health(...). Swapping to our own FastAPI-over-CLI wrapper edits only these.
def client_from_config(cfg: AppConfig) -> SlicerClient | None  # None when slicer_url unset
```
- [ ] Write respx tests: `test_health_contract` (version parsed); `test_slice_contract` (multipart has the model file plus three profile JSON parts and bed type/options, and the response bytes are returned); a non-2xx or non-zip body raises `SlicerError`; a connect error raises `SlicerUnavailable`; `test_client_from_config_none`.
- [ ] Run → FAIL. Implement. Profile JSON is sent as files, not names, so the sidecar never needs our DB. Validate `zipfile.is_zipfile` on the result.
- [ ] Add `Settings.slicer_url` (`AliasChoices("TDMM_SLICER_URL")`) and `AppConfig.slicer_url` to `_env_config`. Test the overlay in `tests/test_app_config_api.py` (one case).
- [ ] Write `compose.slicer.yaml` with service `slicer`: `profiles: [slicer]`, pinned image, `platform: linux/amd64`, `expose: ["3001"]`, a healthcheck on `/health`, and `restart: unless-stopped`. Then add `include: [compose.slicer.yaml]` at the top level of `compose.yaml` and `TDMM_SLICER_URL=http://slicer:3001` to `.env.example`. Stage compose WITHOUT the user's port hunk:
  ```bash
  tmp=$(mktemp); git show HEAD:compose.yaml > "$tmp"   # insert the same include block into $tmp
  git update-index --cacheinfo 100644,"$(git hash-object -w "$tmp")",compose.yaml
  git diff --cached compose.yaml   # must show ONLY the include block; working tree still has 9080
  ```
- [ ] Run `docker compose --profile slicer config -q`, then PASS tests. Commit `feat(slicer): sidecar client contract and compose service`.

### Task 3: Profile bundle import (`inherits` resolution)

**Files:** Create `backend/app/services/slicer_profiles.py`, `backend/tests/test_slicer_profiles.py`, `backend/tests/slicer_fixtures.py` (a tiny BBL tree: `BBL.json`, `machine/` with an abstract `fdm_bbl_3dp_001_common` ← `Bambu Lab A1 mini 0.4 nozzle`, a process `0.20mm Standard @BBL A1M`, a filament chain three levels deep, and one preset with `instantiation:false`).

**Interfaces:**
```python
class ProfileImportError(ValueError): ...
def resolve(name: str, by_name: dict[str, dict], _seen: frozenset[str] = frozenset()) -> dict:
    if name in _seen:
        raise ProfileImportError(f"inherits cycle at {name!r}")
    raw = by_name.get(name)
    if raw is None:
        raise ProfileImportError(f"missing parent {name!r}")
    parent = raw.get("inherits")
    base = resolve(parent, by_name, _seen | {name}) if parent else {}
    merged = {**base, **raw}          # child keys win
    merged.pop("inherits", None)
    return merged
@dataclass class ParsedProfile: kind: ProfileKind; name: str; printer_model: str | None; nozzle_mm: float | None; data: dict
def parse_bundle(root: Path, printer_model: str | None = None) -> list[ParsedProfile]
# root contains BBL.json + machine/process/filament; also accepts a zip (extracted to a tempdir).
# printer: printer_model=data["printer_model"], nozzle_mm=float(data["nozzle_diameter"][0]).
# process/filament: printer_model/nozzle from compatible_printers -> resolved machines (exactly one distinct else None).
# printer_model filter keeps machines of that model plus process/filament compatible with them.
def upsert_profiles(session: Session, parsed: list[ParsedProfile], user_id: int | None) -> dict  # {"created","updated"}
```
- [ ] Tests: `test_resolve_inherits_chain` (grandparent→parent→child override order, no `inherits` key left); cycle and missing-parent errors; `test_parse_bundle_instantiable_only`; `test_printer_model_filter` (A1 mini keeps only its process/filament); `test_import_bundle_upserts` (second import updates, no duplicates); zip input.
- [ ] Run → FAIL. Implement. Never follow paths outside the root (reject `..` and symlinks in zip members). Run → PASS. Commit `feat(slicer): import Bambu Studio BBL profile bundles`.

### Task 4: Profiles, recipes and slicer-health API

**Files:** Create `backend/app/api/profiles.py`, `backend/app/schemas/slicing.py`, `backend/tests/test_profiles_api.py`. Modify `backend/app/api/__init__.py`, `backend/tests/test_auth_sweep.py` (only if its route expectations need the new public/token entries; expect none).

**Interfaces:**
```
GET    /api/profiles?kind=&printer_model=     session            -> ProfileOut[] (no data unless ?full=1)
GET    /api/profiles/{id}                     session            -> ProfileOut (with data)
POST   /api/profiles                          inventory:write    ProfileIn{name,kind,printer_model?,nozzle_mm?,data}
PATCH  /api/profiles/{id}  DELETE /api/profiles/{id}   inventory:write (+ require_owner_or_admin); DELETE 409 if a recipe uses it
POST   /api/profiles/import                   inventory:write    multipart `bundle` (zip) | JSON {path} (path: admin only, must hold BBL.json)
                                                                  | JSON {from_sidecar: true} (SlicerClient.profiles(); 404 if image lacks it)
                                                                  ?printer_model= -> {created, updated}
GET/POST /api/recipes, GET/PATCH/DELETE /api/recipes/{id}   reads session, writes inventory:write (+ owner/admin)
GET    /api/slicer/health                     session            -> {configured, ok, version?, error?}
```
Recipe validation (422): profile kinds must match their FK role; filament ids must be `kind=filament`; `fanout=first_n` needs `n >= 1`; `target_printer_id` needs `ctx.require_printer(..., "print")`. Health lives on a NEW session router `slicer_status_router` (prefix `/slicer`, mounted on `protected_router`), because `slicer.router` is bearer-token only. Health calls the client in `anyio.to_thread` with a 3 s timeout and never raises (it returns `ok=false, error`).
- [ ] Tests: CRUD round trip; viewer 403 on every write; recipe validation matrix; delete-in-use 409; bundle import (uses `slicer_fixtures`); path import 403 for non-admin; health with respx up/down/unconfigured.
- [ ] Run → FAIL → implement → PASS, plus `tests/test_perm_sweep.py tests/test_auth_sweep.py`. Commit `feat(api): slicer profiles, recipes and sidecar health`.

### Task 5: `slice_file` task + revision output

**Files:** Create `backend/app/tasks/slicing.py`, `backend/tests/test_slice_task.py`. Modify `backend/app/tasks/celery_app.py` (`imports` + route `"app.tasks.slicing.*": {"queue": "cpu"}` inserted BEFORE the `app.tasks.*` catch-all).

**Interfaces:**
```python
@celery_app.task(name="app.tasks.slicing.slice_file")
def slice_file(queue_entry_id: int) -> None
def _slice(session, settings, client: SlicerClient, entry) -> int  # returns new File.id
```
Flow: lock the entry row; return unless `state == slicing`. Fetch the source blob to a temp dir (`derivatives.fetch_blob_to_temp`) and load the recipe's three profile `data`s. Call `client.slice(..., options=SliceOptions(auto_orient, arrange, plate=entry.plate))`, then `slicedmeta.parse_gcode_3mf(out)`. The output must contain `entry.plate` if one is set (else plate 1), and its `printer_model` must match the recipe printer profile's. Store it via `library.store_imported_file_sync` as a new File `"<stem> - <recipe name>.gcode.3mf"` on the source file's revision (replace the same rel_path, reusing `slicer_intake`'s pending-store guard). Set `sliced_file_id` and `state=ready`. On `SlicerError`/validation error: `attempts += 1`, `last_error=str(e)`, then `waiting` if `attempts < 3` else `failed`. `SlicerUnavailable` does NOT consume an attempt; the item goes back to `waiting` with `last_error`.
- [ ] Tests (fake `SlicerClient` returning the `archive_fixtures.sliced_3mf_bytes` fixture): `test_slice_success_ready` (new File exists, `sliced_file_id` set, BlobMeta-independent); `test_slice_failure_retries_then_fails` (3 errors → failed, attempts=3); `test_unavailable_no_attempt`; `test_plate_missing_fails`; `test_wrong_printer_model_fails`.
- [ ] Run → FAIL → implement → PASS. Commit `feat(slicer): slice_file task stores sliced revision file`.

### Task 6: Queue API: persisted items, retry, visibility

**Files:** Modify `backend/app/services/queue.py`, `backend/app/api/queue.py`, `backend/app/schemas/queue.py`, `backend/tests/test_queue_api.py`.

**Interfaces:**
```python
class QueueEnqueueIn(BaseModel):
    model_id: int | None = None          # bare legacy entry when file_id is None (idempotent, unchanged)
    file_id: int | None = None; plate: int | None = None; printer_id: int | None = None
    recipe_id: int | None = None; copies: int = Field(1, ge=1, le=50)
    ams_mapping: list[int] | None = None; options: PlanOptions | None = None; front: bool = False
QueueEntryOut (+): printer_id, recipe_id, recipe_name, file_id, sliced_file_id, plate, copies, state,
    assigned_printer_id, print_job_id, attempts, last_error, file_name
POST /api/queue/{id}/retry   printers:print + require_owner_or_admin -> QueueEntryOut; 409 unless failed|held
GET  /api/queue?include_done=  (visibility per Global Constraints)
```
Enqueue with a `file_id`: derive `model_id` from the file's revision. Without a recipe the blob must be `GCODE_3MF` and the item is created `ready`; with a recipe it must be in `SLICEABLE_FORMATS` and is created `waiting`. A set `printer_id` requires `ctx.require_printer(printer_id, "print")`. `front=true` (PrintDialog "Slice and send") inserts at position 1. Non-bare enqueue is never idempotent.
- [ ] Tests: legacy bare enqueue stays idempotent; file enqueue persists plate/printer/mapping/options; recipe enqueue → `waiting` and non-sliced file accepted; as-sliced non-gcode → 422; `front` ordering; `test_retry_endpoint_states` (failed/held → reset, others 409, non-owner 403); visibility hides items on invisible printers.
- [ ] Run → FAIL → implement → PASS (+ `tests/test_perm_sweep.py`). Commit `feat(queue): per-file queue items with printer, recipe and retry`.

### Task 7: Dispatcher (eligibility, compatibility, fanout, retry) + beat

**Files:** Create `backend/app/services/dispatcher.py`, `backend/app/tasks/dispatch.py`, `backend/tests/test_dispatcher.py`. Modify `backend/app/services/print_plan.py` (extract `_check_sendable` + `create_job_sync` + the pure `plan_mapping`), `backend/app/services/permissions.py` (`can_print_sync`), `backend/app/printers/{base,bambu}.py` (`nozzle_mm` from MQTT `nozzle_diameter`), `backend/app/tasks/celery_app.py`.

**Interfaces:**
```python
@dataclass(frozen=True) class Candidate: id: int; idle: bool
def select_printers(fanout: Fanout, n: int | None, candidates: Sequence[Candidate], copies: int) -> list[int]:
    """candidates = compatible + permitted printers in stable (name, id) order; one copy per returned id."""
    pool = list(candidates)
    if fanout == Fanout.FIRST_N:
        pool = pool[: max(1, n or 1)]
    idle = [c.id for c in pool if c.idle]
    if copies < 1 or not idle:
        return []
    if fanout == Fanout.SINGLE:
        return idle[:1]
    return idle[:copies]
def compatibility_reason(printer: Printer, state: dict | None, need: Needs) -> str | None
    # Needs(printer_model, nozzle_mm, filaments[FilamentNeed]) from sliced BlobMeta (or recipe profiles pre-slice).
    # Rejects: model substring mismatch (shared `model_matches` also used by build_plan), |nozzle diff| > NOZZLE_TOL_MM
    # when both known, any plan_mapping status in {"type","none"}.
def printer_hold_reason(client: Redis, printer_id: int) -> str | None   # P8 slot; returns None in P6
def eligible_queue_items(session: Session) -> list[PrintQueueEntry]
    # THE single filter P7/P8 extend: file_id NOT NULL AND state IN (waiting, ready) AND attempts < QUEUE_MAX_ATTEMPTS,
    # ordered by position. P7 adds review_state IN (none, approved) AND hold_reason IS NULL here only.
def dispatch_once(session: Session, client: Redis, settings: Settings) -> dict  # {"sliced","sent","held","retried"}
# print_plan: def plan_mapping(plate_filaments, trays, overrides) -> tuple[list[Match], list[int], bool]
#             def create_job_sync(session, settings, client, printer_id, req: PrintRequest, created_by_user_id) -> PrintJob
# permissions: def can_print_sync(session, user_id: int | None, printer_id: int) -> bool  # inactive user -> False
```
`dispatch_once` order:
1. **Reconcile:** for each `dispatched` item whose job ended `FAILED` with `started_at IS NULL`, apply the retry rule.
2. **Busy set:** printers with a `PrintJob` in `queued|uploading|starting`, plus printers whose Redis state is not in `DISPATCH_IDLE_STATES`, plus `printer_hold_reason` non-None, plus disabled printers.
3. **Items:** for each eligible item:
   - `waiting` + recipe + no sliced file → `slicing` and `slice_file.delay` (≤ `SLICES_PER_TICK`).
   - `ready` + `BlobMeta` present → build candidates (target/model filters → `can_print_sync` → `compatibility_reason`). No permitted candidate → `held`. Compatibility failure on all → `last_error=reason`, stays `ready`.
   - Then `select_printers` excluding busy and already-used-this-tick printers. For each pick: split copies, `create_job_sync` (mapping = stored `ams_mapping` if `assigned == item.printer_id` else `plan_mapping` vs that printer's trays; options from item or recipe), `state=dispatched`, set `assigned_printer_id`/`print_job_id`. Errors → retry rule.
4. Commit per item.

The beat task `app.tasks.dispatch.dispatch_queue` takes the non-blocking `DISPATCH_LOCK_KEY` lock (`timeout=60`) and no-ops when `printer_enabled` is off. Register it additively with `celery_app.conf.beat_schedule["dispatch-queue"] = {"task": ..., "schedule": DISPATCH_INTERVAL_S}`.
- [ ] Tests (`printer_fixtures` + Redis state seeding; monkeypatch `send_to_printer.apply_async` to record):
  - `test_select_printers_matrix`: parametrized `single|spread|first_n(n=1)` × 1–2 printers × idle mixes × copies 1/2.
  - `test_copies_split_single_pins_printer`, `test_one_item_per_printer_per_tick`.
  - `test_compat_rejects_model_nozzle_material`.
  - `test_held_not_dispatched`: both `state=held` and `printer_hold_reason` monkeypatched.
  - `test_revoked_access_holds`: remove the group ACL after enqueue.
  - `test_send_failure_retry`: `create_job_sync` raising, and a job FAILED pre-start, → attempts, then failed at 3.
  - `test_waiting_recipe_kicks_slice`.
  - `test_lock_skips_overlap`.
  - `test_finish_is_idle_failed_is_not`.
  - existing `tests/test_print_plan_api.py tests/test_send_flow.py` still green after the `create_job` refactor.
- [ ] Run → FAIL → implement → PASS. Commit `feat(queue): beat dispatcher with fanout, compatibility and retry`.

### Task 8: Overview per-printer up_next

**Files:** Modify `backend/app/services/printer_overview.py`, `backend/app/schemas/printers.py`, `backend/app/services/queue.py`, `backend/tests/test_printer_overview_api.py`.

**Interfaces:** `PrinterOverviewItem.up_next: list[QueueEntryOut]` holds items with `printer_id == p.id` (or `assigned_printer_id == p.id` and not done) in non-terminal states, limit 3. `PrintersOverviewOut.up_next` keeps the top-level list, now only the unassigned items (`printer_id IS NULL`, file set or bare), limit 3. A single batched query via `queue.list_queue(..., printer_ids=..., limit_per_printer=3)` keeps the overview's query count independent of printer count.
- [ ] Test: two printers, items targeted to each plus unassigned items → correct partition. Assert the query count against the existing overview perf test.
- [ ] Run → FAIL → implement → PASS. Commit `feat(printers): per-printer up next in overview`.

### Task 9: Web ProfilesPage (profiles + recipes tabs)

**Files:** Create `web/src/api/slicing.ts`, `web/src/pages/ProfilesPage.tsx`, `web/src/pages/ProfilesPage.test.tsx`, `web/src/components/inventory/RecipeForm.tsx`, `web/src/components/inventory/ProfileImportDialog.tsx`. Modify `web/src/api/types.ts` and `web/src/routes.tsx` (replace the `/inventory/profiles` Placeholder).

**Interfaces:** hooks `useProfiles(kind)`, `useProfile(id)`, `useImportProfiles()`, `useRecipes()`, `useSaveRecipe()`, `useDeleteRecipe()`, `useSlicerHealth()`; types `SlicerProfile`, `PrintRecipe`, `Fanout`, `SlicerHealth`.
- The Profiles tab has a kind segmented filter plus a table (name, printer model, nozzle, source). Import dialog: zip upload, "From sidecar". A sidecar health chip in the header shows version or "not configured".
- The Recipes tab has a table plus `RecipeForm`: printer → filters process/filament by `printer_model`, per-slot filament selects, bed type select (the BBL `curr_bed_type` values from the printer profile, fallback `Textured PEI Plate`), option toggles, target printer/model, and fanout + `n` (shown only for first_n).
- Writes are hidden without `inventory:write`.
- [ ] Invoke `frontend-design`. Write tests: tab switch, kind filter, recipe form validation (first_n requires n), import posts multipart, viewer sees no write buttons. Run → FAIL → implement → PASS + build + oxlint. Commit `feat(web): profiles and recipes page`.

### Task 10: Web QueuePage lanes + UpNextList per printer

**Files:** Modify `web/src/pages/QueuePage.tsx(+test)`, `web/src/components/print/UpNextList.tsx(+test)`, `web/src/api/queue.ts`, `web/src/api/types.ts`, `web/src/pages/PrintDashboardPage.tsx`, `web/src/pages/PrinterDetailPage.tsx` (+ their tests where they assert `up_next`).

**Interfaces:** `useQueue({ includeDone })`, `useRetryQueueEntry()`, `useEnqueue(body: QueueEnqueueBody)` (the existing `useEnqueueModel` wraps it with `{model_id}`). `QueueStateChip({ state, lastError, attempts })`. `UpNextList({ entries, printerId? })`.
- Lanes are one column per visible printer plus "Any printer" (`printer_id == null`). Dispatched items sit in the `assigned_printer_id` lane. Each row: thumb, file/model, plate, copies, recipe name or "As sliced", state chip (tooltip shows `last_error` and `attempts/3`), and Retry (failed/held) / Remove. Reorder stays within the global positions (existing PATCH).
- PrinterCard/PrinterDetail read the printer's own `up_next`. The dashboard "Up next" shows the unassigned list.
- [ ] Invoke `frontend-design`. Tests: grouping into lanes, chip per state, retry visible only for failed/held, include-done toggle, per-printer UpNextList. Run → FAIL → implement → PASS + build + oxlint. Commit `feat(web): per-printer queue lanes and state chips`.

### Task 11: Web PrintDialog recipe step

**Files:** Modify `web/src/components/print/PrintDialog.tsx(+test)`.

- The Profile step is a radio between "As sliced" (default when the source is `.gcode.3mf`) and a recipe list filtered to recipes whose printer profile `printer_model` matches the chosen printer, or all when none is chosen.
- When a recipe is chosen, the Source step also lists the model's sliceable files (3mf/stl/step/obj). Plan/Filament steps show "Mapped after slicing" (`/print/plan` needs a sliced file), and the footer shows the recipe name instead of estimates.
- Send step:
  - As sliced: **Send now** (unchanged) / **Add to queue**, which now persists `file_id, plate, printer_id, ams_mapping, options` through `useEnqueue` (P5 hook fulfilled).
  - Recipe: **Slice and send** (`useEnqueue({..., recipe_id, printer_id, front: true})`, toast "Slicing, then sending to <printer>") / **Add to queue** (`front: false`). A copies stepper (1–50).
- Disable recipe actions with a tooltip when `useSlicerHealth().ok` is false.
- [ ] Tests: recipe choice swaps the buttons; Slice and send posts `front:true` with recipe; as-sliced Add to queue posts plate/printer/mapping; slicer-down disables. Run → FAIL → implement → PASS + build + oxlint. Commit `feat(web): recipe slicing in print dialog`.

### Task 12: Docs, live-verify checklist, gates

**Files:** Create `docs/superpowers/plans/2026-10-22-p6-live-verify.md`. Modify `README.md` (slicer profile, `TDMM_SLICER_URL`, profile import), `.env.example`.

- Live-verify checklist:
  - [ ] the pinned tag's real endpoints/fields match `_ENDPOINTS` (fix the mapping layer only) and `/health` returns the version
  - [ ] a recipe slice of an A1 Mini model prints: plate index correct, and the md5 of the `.gcode.3mf` the printer received equals the stored blob's
  - [ ] bed-type strings accepted by the CLI vs BBL `curr_bed_type`
  - [ ] MQTT `nozzle_diameter` present on the A1 Mini
  - [ ] spread across 2 printers (or 1 printer + a disabled one), FINISH → next item auto-sends
  - [ ] `docker compose up` without `--profile slicer` still starts everything
- [ ] Run the spec's Verification block (controller). Commit `docs(slicer): P6 live-verify checklist and setup`.

---

## Self-review + pre-flight conflict scan (resolved inline)

- **Spec coverage:** every column, task, beat entry, API route, UI item, test bullet and live-verify bullet in §P6 maps to Tasks 1–12. The "flag set for pinned version" bullet maps to `SLICER_TAG` + the health version.
- **`compose.yaml` dirty hunk:** handled by the `include:` + index-only staging in Task 2.
- **`/api/slicer/*` bearer plane:** health goes on a separate session router. The perm sweep's `_TOKEN_PLANE` skips only mutating routes, and health is a GET.
- **`print_queue.model_id UNIQUE`:** replaced by a partial unique index, so the legacy idempotent enqueue tests keep passing.
- **The scheduler's 15 s `_CADENCE_S`:** untouched. `dispatch-queue` is a separate 10 s entry with its own lock key.
- **Celery routing order:** `slicing.*` is inserted before the catch-all.
- **`create_job` is async with `AuthContext`:** the dispatcher uses `create_job_sync`, which shares `_check_sendable`. The routes still call the async `create_job`.
- **Overview schema change:** web types and tests are updated in Task 10, in the same task as their consumers.
