# P2 Print Archive, Run History and Cost Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every terminal print (sent from the app or started from Studio/touchscreen) becomes an archived run: the 3MF, thumbnail, per-tray estimated grams, result, duration and a server-computed cost on an auto `prints` row the user can grade Good/Reject.

**Architecture:** One migration adds `print_archives`, `print_jobs.origin`/`plate` (`file_id` nullable) and run/cost columns on `prints` (`model_id` nullable). printerd derives the run result on a terminal transition and enqueues `archive_print` (io queue, 3 retries); it also creates `origin=external` jobs for prints it didn't send. `services/archive.py` gets the 3MF (local blob for app jobs, FTPS via new optional adapter methods for external ones), parses it with `pipeline/slicedmeta.py`, resolves the model, stores files content-addressed under `{data_dir}/archives/`, and upserts the archive + auto `prints` row priced by pure `services/costing.py`. New `/api/archives` router; the web history table and a run-detail sheet read it; client-side `printCost.ts` is retired.

**Tech Stack:** FastAPI, SQLAlchemy 2 (async API, sync worker/printerd), Alembic, Postgres, Celery, Redis, vendored `bambulabs_api` FTPS client, pytest + testcontainers; React + TanStack Query, vitest.

**Spec:** `docs/superpowers/specs/2026-10-21-bambuddy-parity-design.md` (§Context, §Phase order, §Integration rule, §P2)

## Global Constraints

- Branch `p2-print-archive`, stacked on `p1-multiuser-oidc`. One Alembic migration `d8b3f2a6c4e9` revising `c5e2a8d4b7f1`; `tests/test_migration_drift.py` stays green.
- Enums (all `str_enum`, `native_enum=False`): `PrintJobOrigin` = `app|external`; `ArchiveResult` = `completed|failed|canceled|stopped`; `ArchiveFetchState` = `pending|done|missing|error`; `PrintSource` = `manual|auto`; `PrintQuality` = `good|reject` (column nullable).
- `print_archives`: `print_job_id` FK unique (CASCADE), `model_id`/`revision_id` nullable (SET NULL), `archive_blob`, `thumbnail_blob`, `snapshot_blob`, `timelapse_blob` (CHAR(64) sha256, nullable), `result`, `duration_s`, `est_filament` JSONB `[{tray,type,color,grams,meters}]`, `plate`, `slicer_meta` JSONB, `fetch_state`, `fetch_error`, `created_by_user_id` (nullable FK users, `ON DELETE SET NULL`, indexed), `created_at`.
- `prints` + `print_job_id` (FK `print_jobs`, unique, nullable, SET NULL), `source` (default `manual`), `quality`, `failure_reason`, `filament_cost`, `machine_cost`, `energy_kwh`, `energy_cost`, `total_cost` (floats, nullable).
- Cost: `filament_cost = grams/1000 × filament_cost_per_kg`; `machine_cost = hours × machine_cost_per_hour`; rates read from `AppConfig` (`services/app_config.py`) at pricing time. Failed/canceled/stopped runs are charged `est_g × layer/total_layers`. Energy stays NULL until P8.
- External 3MF location: `/cache/<subtask>.3mf` (spec expectation; live-verified in Task 12).
- `archive_print` runs on the `io` queue (worker-io; `app.tasks.*` already routes there), `max_retries=3` with backoff.
- Permissions: archive reads use `visible_printer_ids(ctx)` (admins see all; hidden = 404). `PATCH /api/prints/{id}` → `library:write` + `require_owner_or_admin`. Reprint stub → `printers:print`. Every new mutating route must pass `tests/test_perm_sweep.py` without an allowlist entry.
- Integration rule: `print_archives.created_by_user_id` and the auto `prints.created_by_user_id` copy `print_jobs.created_by_user_id` (NULL for external jobs); the task never invents an owner.
- Leave the uncommitted `compose.yaml` change alone. Commit as metril, conventional subjects, no AI mention.
- Gates: `cd backend && uv run pytest tests/<file> -q` (Docker, sandbox disabled), `uv run ruff check . && uv run ruff format --check .`; `cd web && npx vitest run <file>`, `npm run build`, `npx oxlint`. Full gate per spec §Verification before the branch review.

## Review Focus

- Idempotency: running `archive_job` twice for one job leaves one `print_archives` row, one auto `prints` row and `print_count` bumped once (Task 4 `test_archive_is_idempotent`).
- Partial cost with `total_layers` 0/None never divides by zero and never charges more than the estimate (Task 2 `test_charged_grams_partial_edges`).
- A member without printer access sees archives of that printer neither in the list nor by id/thumbnail/file (404) (Task 7 `test_archive_visibility`).
- External detection creates exactly one `external` job across repeated RUNNING reports and never one while an app job is active (Task 5 `test_external_job_detection`).
- `PATCH /api/prints/{id}`: a member cannot edit another user's or a NULL-owner run; edits recompute only the changed cost component (Task 6 `test_patch_run_ownership_and_cost`).

---

### Task 1: Migration + models + enums

**Files:**
- Create: `backend/alembic/versions/d8b3f2a6c4e9_print_archive_run_history.py`
- Modify: `backend/app/models/enums.py`, `backend/app/models/printing.py` (PrintJob + new `PrintArchive`), `backend/app/models/library.py` (Print), `backend/app/models/__init__.py`
- Test: `backend/tests/test_migrations_p2.py`

**Interfaces:**
- Produces: enums from Global Constraints. `PrintJob.file_id: int | None`, `PrintJob.origin: PrintJobOrigin` (server default `app`), `PrintJob.plate: int | None`. `PrintArchive` with the Global Constraints columns. `Print.model_id: int | None`, `Print.print_job_id: int | None` (unique), `Print.source: PrintSource` (server default `manual`), `Print.quality: PrintQuality | None`, `Print.failure_reason: str | None`, `Print.filament_cost/machine_cost/energy_kwh/energy_cost/total_cost: float | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migrations_p2.py (scratch_db/_run_alembic from tests/migration_helpers.py)
async def test_p2_round_trip_keeps_manual_prints(scratch_db):
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, "c5e2a8d4b7f1")
    # seed models(slug='m', name='M') + prints(model_id) via asyncpg
    await _run_alembic(sa_url, command.upgrade, "d8b3f2a6c4e9")
    assert await conn.fetchval("select source from prints") == "manual"
    assert await conn.fetchval("select origin from print_jobs limit 1") in (None, "app")
    assert await conn.fetchval("select to_regclass('print_archives')") is not None
    await _run_alembic(sa_url, command.downgrade, "c5e2a8d4b7f1")
    assert await conn.fetchval("select to_regclass('print_archives')") is None
    await _run_alembic(sa_url, command.upgrade, "d8b3f2a6c4e9")

async def test_p2_downgrade_drops_external_jobs_and_null_model_prints(scratch_db): ...
    # an external job (file_id NULL) and an auto print (model_id NULL) are deleted on downgrade
    # so the restored NOT NULL constraints apply.
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_migrations_p2.py tests/test_migration_drift.py -q` → FAIL (unknown revision `d8b3f2a6c4e9`).
- [ ] **Step 3: Implement** enums, model columns, `PrintArchive` (relationship-free; index `print_archives.created_by_user_id`, `print_archives.model_id`, `print_archives.result`), and the migration (create/drop in reverse; downgrade deletes `print_jobs where file_id is null` and `prints where model_id is null` before restoring NOT NULL). Mirror `c5e2a8d4b7f1`'s `sa.Enum(..., native_enum=False, create_constraint=True)` style.
- [ ] **Step 4: Run** same command → PASS.
- [ ] **Step 5: Commit** `git add -A backend && git -c user.name=metril -c user.email=1517921+metril@users.noreply.github.com commit -m "feat(db): add print archives, run origin and run cost columns"`

### Task 2: Pure costing service

**Files:**
- Create: `backend/app/services/costing.py`
- Test: `backend/tests/test_costing.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class CostRates: filament_per_kg: float; machine_per_hour: float` + `@classmethod from_config(cls, cfg: AppConfig) -> CostRates`
  - `@dataclass(frozen=True) class RunCost: filament_cost: float | None; machine_cost: float | None; energy_cost: float | None; total_cost: float | None`
  - `def charged_grams(est_g: float | None, result: ArchiveResult, *, layer: int | None, total_layers: int | None, progress_pct: float | None) -> float | None`
  - `def price_run(filament_g: float | None, duration_s: int | None, rates: CostRates, *, energy_cost: float | None = None) -> RunCost`

- [ ] **Step 1: Write the failing test**

```python
R = CostRates(filament_per_kg=20.0, machine_per_hour=1.5)

def test_price_run_sums_components():
    c = price_run(250.0, 7200, R)
    assert (c.filament_cost, c.machine_cost, c.total_cost) == (5.0, 3.0, 8.0)

def test_price_run_unknowns():
    assert price_run(None, None, R).total_cost is None
    assert price_run(100.0, None, R) == RunCost(2.0, None, None, 2.0)

def test_charged_grams_completed_is_full_estimate():
    assert charged_grams(100.0, ArchiveResult.COMPLETED, layer=10, total_layers=200, progress_pct=5) == 100.0

def test_charged_grams_partial_by_layer():
    for r in (ArchiveResult.FAILED, ArchiveResult.CANCELED, ArchiveResult.STOPPED):
        assert charged_grams(100.0, r, layer=50, total_layers=200, progress_pct=None) == 25.0

def test_charged_grams_partial_edges():
    assert charged_grams(100.0, ArchiveResult.FAILED, layer=5, total_layers=0, progress_pct=40) == 40.0
    assert charged_grams(100.0, ArchiveResult.FAILED, layer=None, total_layers=None, progress_pct=None) == 0.0
    assert charged_grams(100.0, ArchiveResult.FAILED, layer=300, total_layers=200, progress_pct=None) == 100.0
    assert charged_grams(None, ArchiveResult.FAILED, layer=1, total_layers=2, progress_pct=None) is None
```

- [ ] **Step 2: Run** `uv run pytest tests/test_costing.py -q` → FAIL (ImportError).
- [ ] **Step 3: Implement** in `costing.py`: layer ratio clamped to `[0, 1]`; fallback `progress_pct/100` when `total_layers` falsy; else `0.0`. `total_cost` = sum of non-None components, None if all None. Round nothing (UI formats).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `... commit -m "feat(costing): add server-side run cost and partial-print charging"`

### Task 3: Adapter `list_dir` / `download_file`

**Files:**
- Modify: `backend/app/printers/base.py`, `backend/app/printers/bambu.py`, `backend/app/printers/fake.py`
- Test: `backend/tests/test_adapter_files.py`

**Interfaces:**
- Produces (on `PrinterAdapter`, non-abstract, default `raise NotImplementedError`):
  - `def list_dir(self, path: str) -> list[str]` (bare file names)
  - `def download_file(self, remote: str, dest: Path) -> None` (raises `OSError` on failure)
- `bambu.py`: `def _build_ftp(conn: PrinterConnection)` → lazy `from bambulabs_api.ftp_client import PrinterFTPClient; return PrinterFTPClient(conn.host, conn.access_code)` (monkeypatched in tests). The vendored `connect_and_run` swallows exceptions and returns `None`, so `None` from `list_directory`/`download_file` → `OSError("FTPS ... failed")` (never include the access code).
- `fake.py`: instance `files: dict[str, bytes]`, `download_errors: int = 0` (raise `OSError` and decrement while > 0), `downloads: list[str]` (appended on every attempt, before any raise).

- [ ] **Step 1: Write the failing test**

```python
def test_bambu_list_dir_parses_names_with_spaces(monkeypatch):
    stub = SimpleNamespace(list_directory=lambda p: ("226", [
        "-rw-r--r-- 1 root root 2298680 Jan 01 00:00 DotC parts - Part 1_plate_1.3mf"]))
    monkeypatch.setattr(bambu, "_build_ftp", lambda conn: stub)
    assert _adapter().list_dir("/cache") == ["DotC parts - Part 1_plate_1.3mf"]

def test_bambu_download_writes_dest_and_raises_on_none(monkeypatch, tmp_path):
    stub = SimpleNamespace(download_file=lambda p: io.BytesIO(b"3mf"))
    monkeypatch.setattr(bambu, "_build_ftp", lambda conn: stub)
    _adapter().download_file("/cache/x.3mf", tmp_path / "x")
    assert (tmp_path / "x").read_bytes() == b"3mf"
    stub.download_file = lambda p: None
    with pytest.raises(OSError):
        _adapter().download_file("/cache/x.3mf", tmp_path / "y")

def test_base_methods_default_not_implemented(): ...  # minimal subclass -> NotImplementedError

def test_fake_files_and_errors(tmp_path):
    a = FakePrinterAdapter(conn); a.files["/cache/a.3mf"] = b"z"; a.download_errors = 1
    assert a.list_dir("/cache") == ["a.3mf"]
    with pytest.raises(OSError): a.download_file("/cache/a.3mf", tmp_path / "a")
    a.download_file("/cache/a.3mf", tmp_path / "a"); assert (tmp_path / "a").read_bytes() == b"z"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_adapter_files.py -q` → FAIL.
- [ ] **Step 3: Implement**: names = `line.split(maxsplit=8)[8]` for lines with ≥ 9 fields; skip `.`/`..`. Fake `list_dir` returns names of keys whose parent equals `path.rstrip("/") or "/"`; missing key → `FileNotFoundError`.
- [ ] **Step 4: Run** → PASS (plus `tests/test_printer_registry.py`).
- [ ] **Step 5: Commit** `... commit -m "feat(printers): add FTPS list_dir and download_file adapter methods"`

### Task 4: Archive service

**Files:**
- Create: `backend/app/services/archive.py`, `backend/tests/archive_fixtures.py` (register in `conftest.pytest_plugins`)
- Test: `backend/tests/test_archive_service.py`

**Interfaces:**
- Consumes: Task 1 models, Task 2 `CostRates`/`charged_grams`/`price_run`, Task 3 adapter methods, `derivatives.fetch_blob_to_temp`/`publish_bytes`, `slicedmeta.parse_gcode_3mf`, `gcode3mf.plates_in_gcode_3mf`, `slicer_naming.model_name_from_filename`/`_safe_basename`, `slicer_intake._find_model_by_name_ci_sync`, `connection_from_printer`, `build_adapter`, `get_app_config_sync`.
- Produces:
  - `class ArchiveFetchError(RuntimeError)` (transient FTPS failure; raised after the row is committed with `fetch_state=error`)
  - `def archive_path(settings: Settings, sha: str, ext: str) -> Path` → `{data_dir}/archives/<sha[:2]>/<sha>.<ext>`
  - `def archive_job(session: Session, settings: Settings, print_job_id: int, result: ArchiveResult) -> PrintArchive`
  - `RESULT_TO_PRINT_RESULT = {COMPLETED: SUCCESS, FAILED: FAIL, CANCELED: PARTIAL, STOPPED: PARTIAL}`
- Fixture `sliced_3mf_bytes` = `tests/corpus_real/DotC parts - Part 1_plate_1.gcode.3mf` bytes; fixture `archived_job(origin, subtask, ...)` seeds printer + job (sync session).

- [ ] **Step 1: Write the failing test**

```python
def test_archive_external_job_from_ftps(fake_adapter, sliced_3mf_bytes, printer_enabled, data_dir, seed):
    model_id = seed.model(name="DotC parts - Part 1")
    job_id = seed.job(origin="external", file_id=None, subtask="DotC parts - Part 1_plate_1",
                      started=..., finished=+3600s, layer=474, total_layers=474)
    fake_adapter.files["/cache/DotC parts - Part 1_plate_1.3mf"] = sliced_3mf_bytes
    arc = archive_job(s, settings, job_id, ArchiveResult.COMPLETED)
    assert arc.fetch_state == "done" and arc.plate == 1 and arc.model_id == model_id
    assert arc.est_filament == [{"tray": 0, "type": "PLA", "color": "#000000", "grams": 61.64, "meters": 21.18}]
    assert archive_path(settings, arc.archive_blob, "gcode.3mf").exists()
    assert arc.thumbnail_blob is not None and arc.duration_s == 3600
    run = s.scalar(select(Print).where(Print.print_job_id == job_id))
    assert run.source == "auto" and run.result == "success" and run.created_by_user_id is None
    assert run.filament_g == pytest.approx(61.64)
    assert run.filament_cost == pytest.approx(61.64 / 1000 * 20.0)  # default filament_cost_per_kg

def test_archive_app_job_uses_local_blob(...):  # file_id set -> no FTPS call, model/revision from File, plate from job.plate
    assert fake_adapter.downloads == [] and arc.revision_id == rev_id
    assert run.created_by_user_id == job_creator_id == arc.created_by_user_id

def test_archive_partial_cost_on_cancel(...):  # layer 237/474, CANCELED -> filament_g == approx(30.82), result 'partial'

def test_archive_missing_file(...):  # empty fake -> fetch_state 'missing', est_filament [], run.filament_cost is None,
                                      # machine_cost from duration still set

def test_archive_fetch_error_raises_after_commit(...):  # download_errors=1 -> ArchiveFetchError; row fetch_state 'error'

def test_archive_is_idempotent(...):  # archive_job twice -> 1 archive, 1 run, Model.print_count == 1

def test_archive_subtask_traversal_is_sanitized(...):  # subtask "../../etc/x" -> only '/cache/x.*' names probed
```

- [ ] **Step 2: Run** `uv run pytest tests/test_archive_service.py -q` → FAIL.
- [ ] **Step 3: Implement** `archive_job`:
  1. Load job + printer; upsert `PrintArchive` by `print_job_id` (`fetch_state=pending`, `created_by_user_id=job.created_by_user_id`, `result`).
  2. Locate bytes: `job.file_id` → `fetch_blob_to_temp`; else (or on `LookupError`) FTPS: `name = _safe_basename(job.subtask_name)`, candidates `"<name>.gcode.3mf"`, `"<name>.3mf"` in dirs `("/cache", "/")` matched against `adapter.list_dir(dir)`; app jobs also try `/cache/tdmm-{job.id}.gcode.3mf`. None found → `missing`; `OSError` → `error` (+ `fetch_error`, scrubbed of the access code), remember to raise later. Always `adapter.close()`.
  3. With bytes: sha256 → `publish_bytes(archive_path(..., "gcode.3mf"))`; `parse_gcode_3mf`; `plate = job.plate or plates_in_gcode_3mf(data)[0]`; `est_filament` from that plate's filaments (`tray` = list position); `slicer_meta = asdict(meta)` minus `plates`; thumbnail = zip member `plate["thumbnail_file"]` → `archive_path(..., "png")`.
  4. Model: app job → `File.revision_id` → `Revision.model_id`; else archive sha == `Blob.hash` → first `File` → revision; else `_find_model_by_name_ci_sync(model_name_from_filename(subtask))`.
  5. `duration_s = finished_at - started_at` (None if either missing). Auto `prints` upsert by `print_job_id`: `printer_name`, `filament = ", ".join(unique types) or None`, `filament_g = charged_grams(sum grams, ...)`, `duration_min = round(duration_s/60)`, `printed_at = finished_at`, `result = RESULT_TO_PRINT_RESULT[result]`, costs from `price_run(..., CostRates.from_config(get_app_config_sync(...)))`. Bump `Model.print_count` only when inserting a run with a model.
  6. Commit; then raise `ArchiveFetchError` if step 2 hit `error`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `... commit -m "feat(archive): archive finished runs with 3MF, thumbnail, grams and cost"`

### Task 5: Celery task + printerd hooks

**Files:**
- Create: `backend/app/tasks/archive.py`
- Modify: `backend/app/printerd.py`, `backend/app/tasks/celery_app.py` (add `"app.tasks.archive"` to `imports=(...)`), `backend/app/api/printers.py` (`start_print` sets `PrintJob.plate`)
- Test: `backend/tests/test_archive_task.py`, `backend/tests/test_printerd.py`

**Interfaces:**
- Consumes: `archive_job`, `ArchiveFetchError`.
- Produces:
  - `@celery_app.task(name="app.tasks.archive.archive_print", bind=True, autoretry_for=(ArchiveFetchError,), max_retries=3, retry_backoff=30, retry_backoff_max=600, retry_jitter=True) def archive_print(self, print_job_id: int, result: str) -> None`
  - `printerd.CANCEL_ERROR_CODES: frozenset[int] = frozenset({50348044})` (0x0300400C; live-verified in Task 12)
  - `printerd.terminal_result(state: PrintJobState, print_error: int | None, stop_requested: bool) -> ArchiveResult`: FINISHED→completed; stop_requested→stopped; error in CANCEL_ERROR_CODES→canceled; else failed.
  - `printerd._enqueue_archive(print_job_id: int, result: ArchiveResult) -> None` → `archive_print.apply_async(args=[print_job_id, result.value])` (tests monkeypatch it).
  - `PrinterWorker._stop_requested: bool` set by `handle_command("stop")`, cleared after a terminal transition.

- [ ] **Step 1: Write the failing tests**

```python
# test_printerd.py
def test_terminal_transition_enqueues_archive(worker, monkeypatch):
    calls = []; monkeypatch.setattr(printerd, "_enqueue_archive", lambda j, r: calls.append((j, r)))
    w, adapter, pid, _ = worker; job_id = _seed_active_job(pid)
    w.handle_report({"gcode_state": "RUNNING"}); w.handle_report({"gcode_state": "FINISH"})
    assert calls == [(job_id, ArchiveResult.COMPLETED)]

def test_cancel_code_and_app_stop(worker, monkeypatch):
    # FAILED + print_error 50348044 -> job.state 'canceled', result CANCELED
    # handle_command("stop") then FAILED -> result STOPPED, job.state 'canceled'
    # FAILED + print_error 1234 -> job.state 'failed', result FAILED

def test_external_job_detection(worker, monkeypatch):
    w, adapter, pid, _ = worker
    w.handle_report({"gcode_state": "RUNNING", "subtask_name": "Benchy", "total_layer_num": 100})
    w.handle_report({"gcode_state": "RUNNING", "subtask_name": "Benchy", "layer_num": 2})
    jobs = _jobs(pid); assert len(jobs) == 1
    assert (jobs[0].origin, jobs[0].file_id, jobs[0].state, jobs[0].subtask_name) == ("external", None, "printing", "Benchy")
    assert jobs[0].created_by_user_id is None and jobs[0].started_at is not None

def test_no_external_job_while_app_job_active(worker): ...  # seeded STARTING job + RUNNING -> still 1 job, now 'printing'

# test_archive_task.py
def test_archive_task_retries_then_succeeds(fake_adapter, sliced_3mf_bytes, seed, ...):
    fake_adapter.download_errors = 2
    archive_print.apply(args=[job_id, "completed"])
    assert _archive(job_id).fetch_state == "done" and len(fake_adapter.downloads) == 3
```

- [ ] **Step 2: Run** `uv run pytest tests/test_printerd.py tests/test_archive_task.py -q` → FAIL.
- [ ] **Step 3: Implement**: task wraps `archive_job` in `base.sync_session()`. In `_transition_active_job`: if no active job and `public.gcode_state == "RUNNING"` → insert `PrintJob(origin=EXTERNAL, file_id=None, state=PRINTING, started_at=now, subtask_name, layer/total_layers)`, publish event, return. On terminal: compute `terminal_result`, set `job.state` to `CANCELED` for canceled/stopped, commit, publish, then `_enqueue_archive` (wrapped in `try/except Exception: log type only`). Also persist `PrintJob.plate = options["plate"]` in `api/printers.start_print`.
- [ ] **Step 4: Run** → PASS (whole `test_printerd.py`, `test_send_flow.py`).
- [ ] **Step 5: Commit** `... commit -m "feat(printerd): archive runs on terminal state and track external prints"`

### Task 6: Run fields, cost on prints, PATCH ownership

**Files:**
- Modify: `backend/app/schemas/prints.py`, `backend/app/services/prints.py`, `backend/app/api/prints.py`, `backend/app/schemas/printers.py` (`PrintJobOut.file_id: int | None`, `+ origin`), `backend/app/services/stats.py` (`_recent_prints` → `outerjoin(Model)`)
- Test: `backend/tests/test_prints_api.py`

**Interfaces:**
- Consumes: `CostRates`, `price_run`, `get_app_config`.
- Produces: `PrintOut` + `model_id: int | None`, `print_job_id`, `source`, `quality`, `failure_reason`, `filament_cost`, `machine_cost`, `energy_kwh`, `energy_cost`, `total_cost`, `created_by_user_id`. `PrintPatchIn` + `quality: PrintQuality | None`, `failure_reason: str | None = Field(None, max_length=500)`. `create_print(..., rates: CostRates)` and `patch_print(db, print_id, changes, rates: CostRates)`. `delete_print` skips the `print_count` update when `model_id is None`.

- [ ] **Step 1: Write the failing test**

```python
async def test_manual_print_gets_server_cost(client, model):  # default rates 20/kg, 0/h
    r = await client.post(f"/api/models/{model.id}/prints", json={"filament_g": 250, "duration_min": 120})
    assert r.json()["filament_cost"] == 5.0 and r.json()["total_cost"] == 5.0 and r.json()["source"] == "manual"

# make_auto_run / other_member_run: local fixtures in this file (insert Print(source='auto', ...) directly)
async def test_patch_run_ownership_and_cost(member_client, client, make_auto_run, other_member_run):
    assert (await member_client.patch(f"/api/prints/{other_member_run}", json={"quality": "good"})).status_code == 403
    assert (await member_client.patch(f"/api/prints/{make_auto_run(owner=None)}", json={"quality": "good"})).status_code == 403
    own = make_auto_run(owner=member.id, filament_g=100, machine_cost=3.0)
    r = await member_client.patch(f"/api/prints/{own}", json={"quality": "reject", "failure_reason": "spaghetti", "filament_g": 50})
    assert r.json()["quality"] == "reject" and r.json()["filament_cost"] == 1.0
    assert r.json()["machine_cost"] == 3.0 and r.json()["total_cost"] == 4.0

async def test_delete_model_less_run(client, make_auto_run): ...  # model_id None -> 204
async def test_recent_prints_include_model_less_runs(client, ...): ...
```

- [ ] **Step 2: Run** `uv run pytest tests/test_prints_api.py tests/test_print_jobs_api.py -q` → FAIL.
- [ ] **Step 3: Implement**: recompute `filament_cost` only when `filament_g` changed, `machine_cost` only when `duration_min` changed, always recompute `total_cost` as the sum of non-None components (incl. `energy_cost`). Add `require_owner_or_admin(ctx, row.created_by_user_id)` to PATCH (404 before 403 for unknown ids).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `... commit -m "feat(prints): server-side run cost, quality grading and owner-gated edits"`

### Task 7: Archives API

**Files:**
- Create: `backend/app/api/archives.py`, `backend/app/schemas/archives.py`
- Modify: `backend/app/api/__init__.py` (`protected_router.include_router(archives.router)`), `backend/tests/test_auth_sweep.py` (`"archive_id": "1"`)
- Test: `backend/tests/test_archives_api.py`

**Interfaces:**
- Consumes: `PrintArchive`, `PrintOut`, `archive_path`, `visible_printer_ids`, `require_perm`.
- Produces: `ArchiveOut{id, print_job_id, printer_id, printer_name, origin, subtask_name, model_id, model_slug, model_name, revision_id, result, duration_s, est_filament, plate, fetch_state, fetch_error, has_thumbnail, has_file, started_at, finished_at, created_at, created_by_user_id, run: PrintOut | None}`.
  - `GET /api/archives?printer=&result=&model=&limit=50(1..200)&offset=0` → `list[ArchiveOut]`, newest first, visible printers only.
  - `GET /api/archives/{archive_id}` → `ArchiveOut`; `GET /{archive_id}/thumbnail` → `image/png`; `GET /{archive_id}/file` → 3MF download (`<subtask>.gcode.3mf`). Missing/hidden → 404.
  - `POST /api/archives/{archive_id}/reprint` (`require_perm("printers:print")`) → 404 if hidden, else `501 {"detail": "reprint arrives with the P5 print flow"}`.

- [ ] **Step 1: Write the failing test**

```python
async def test_list_filters(client, archives):  # 3 archives on 2 printers, results completed/failed
    assert len((await client.get("/api/archives?result=failed")).json()) == 1
    assert {a["printer_id"] for a in (await client.get(f"/api/archives?printer={p1}")).json()} == {p1}
    assert (await client.get(f"/api/archives?model={m}")).json()[0]["run"]["total_cost"] is not None

async def test_archive_visibility(member_client, archives_on_hidden_printer):
    assert (await member_client.get("/api/archives")).json() == []
    for suffix in ("", "/thumbnail", "/file"):
        assert (await member_client.get(f"/api/archives/{aid}{suffix}")).status_code == 404

async def test_thumbnail_served(client, archive_with_thumb): ...  # 200, content-type image/png
async def test_reprint_stub(client, archive): assert (await client.post(f"/api/archives/{aid}/reprint")).status_code == 501
```

- [ ] **Step 2: Run** `uv run pytest tests/test_archives_api.py tests/test_auth_sweep.py tests/test_perm_sweep.py -q` → FAIL.
- [ ] **Step 3: Implement**: one select joining `PrintJob`, `Printer`, outer `Model`, outer `Print` (on `print_job_id`), `selectinload(Print.material)`; file paths checked with `anyio.to_thread.run_sync`. No `require_printer_enabled` gate (history outlives the flag). No allowlist entry in `test_perm_sweep.py` (viewer lacks `printers:print` → 403).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `... commit -m "feat(api): add print archive listing, detail, media and reprint stub"`

### Task 8: Stats read auto runs

**Files:**
- Modify: `backend/app/services/stats.py`, `backend/app/schemas/stats.py` (`PrintsStats.cost_total: float`)
- Test: `backend/tests/test_stats_api.py`

**Interfaces:**
- Consumes: `Print.source`, `PrintArchive.est_filament`.
- Produces: `_material_usage` excludes `source=auto` from the free-text grouping and adds auto runs per archive filament type: `grams_type × (run.filament_g / Σ est grams)` (skip when Σ = 0 or `filament_g` NULL), merged case-insensitively into the same-named free-text group (`material_id=None`); `prints` counts each run once per type. `PrintsStats.cost_total = coalesce(sum(total_cost), 0)`.

- [ ] **Step 1: Write the failing test**

```python
async def test_material_usage_includes_auto_runs(client, seed):
    # manual print filament "PLA" 10 g; auto run filament_g 50, archive est [PLA 60, PETG 40], total_cost 2.5
    usage = {u["name"]: u for u in (await client.get("/api/stats")).json()["material_usage"]}
    assert usage["PLA"]["grams"] == pytest.approx(40.0) and usage["PLA"]["prints"] == 2
    assert usage["PETG"]["grams"] == pytest.approx(20.0) and usage["PETG"]["prints"] == 1
    assert (await client.get("/api/stats")).json()["prints"]["cost_total"] == pytest.approx(2.5)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_stats_api.py -q` → FAIL (reset the stats cache like the existing `_reset_stats_cache` fixture).
- [ ] **Step 3: Implement**: one extra query for auto runs joined to `print_archives`, merged in Python.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `... commit -m "feat(stats): count archived runs in material usage and cost totals"`

### Task 9: Server-side plate cost estimate

**Files:**
- Modify: `backend/app/schemas/library.py` (`PlateOut.est_cost: float | None`, `from_raw(raw, *, thumbnail_available, rates: CostRates)`), `backend/app/services/library.py` (`_build_file_enrichments(settings, blobs, rates)`; its 3 callers load `CostRates.from_config(await get_app_config(db, settings))` once)
- Test: `backend/tests/test_models_api.py`

**Interfaces:**
- Consumes: `price_run`, `CostRates`.
- Produces: `PlateOut.est_cost = price_run(weight_g, prediction_s, rates).total_cost`.

- [ ] **Step 1: Write the failing test**

```python
async def test_plate_est_cost(client, ...):  # seed like the existing `meta.plates` test (~line 1586) with weight_g 250, prediction_s 7200; set rates 20/kg, 1.5/h via PUT /api/settings/app
    plate = (await client.get(f"/api/models/{slug}")).json()["revisions"][0]["files"][0]["meta"]["plates"][0]
    assert plate["est_cost"] == 8.0
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `... commit -m "feat(library): serve per-plate cost estimates from the server"`

### Task 10: Web archive hooks, history table, run detail sheet

**Files:**
- Create: `web/src/api/archives.ts`, `web/src/api/archives.test.ts`, `web/src/components/print/RunDetailSheet.tsx`, `web/src/components/print/RunDetailSheet.test.tsx`, `web/src/components/printer/PrintJobHistory.test.tsx`
- Modify: `web/src/api/types.ts` (`ArchiveOut`, `ArchiveResult`, `FetchState`, `EstFilament`, `PrintEntry` new fields + `model_id: number | null`, `PrintPatchIn` + `quality`/`failure_reason`, `PrintJobOut.file_id: number | null` + `origin`, `PlateOut.est_cost`), `web/src/components/printer/PrintJobHistory.tsx`, `web/src/lib/format.ts` (`formatCost`)

**Interfaces:**
- Produces:
  - `archivesRefetchInterval(rows: ArchiveOut[] | undefined): number | false` → `5000` while any `fetch_state === "pending"`, else `false`
  - `useArchives(filters: { printerId?: number; result?: ArchiveResult; modelId?: number })`, `useArchive(id: number | null)`, `usePatchRun()` (`PATCH /prints/{id}`; invalidates `["archives"]` and `["prints"]`), `archiveThumbnailUrl(id: number): string`, `archiveFileUrl(id: number): string`
  - `formatCost(value: number | null | undefined): string` → `"--"` or `toFixed(2)`
  - `RunDetailSheet({ archiveId, onOpenChange }: { archiveId: number | null; onOpenChange: (open: boolean) => void })`

- [ ] **Step 1: Write the failing tests**

```ts
// archives.test.ts
expect(archivesRefetchInterval([{ fetch_state: "pending" } as ArchiveOut])).toBe(5000);
expect(archivesRefetchInterval([{ fetch_state: "done" } as ArchiveOut])).toBe(false);
// PrintJobHistory.test.tsx (vi.mock "@/api/archives")
// renders a row per archive: model_name ?? subtask_name, result badge, duration, grams, formatCost(run.total_cost);
// clicking a row opens the sheet (dialog with the run title); result filter select narrows useArchives({ result })
// RunDetailSheet.test.tsx
// Good -> usePatchRun called with { id: run.id, patch: { quality: "good" } };
// Reject reveals "Failure reason" input; save sends { quality: "reject", failure_reason: "spaghetti" };
// cost rows: Filament 5.00, Machine 3.00, Energy --, Total 8.00;
// grading controls absent (not disabled) for a viewer or a non-owner member; no Reprint control rendered
```

- [ ] **Step 2: Run** `cd web && npx vitest run src/api/archives.test.ts src/components/print src/components/printer` → FAIL.
- [ ] **Step 3: Implement**: table columns Thumbnail (`archiveThumbnailUrl`, hidden when `!has_thumbnail`), Name, Printer, Result, Duration, Grams, Cost, Quality, Finished; filters printer/result. Sheet uses `@/components/ui/sheet`, shows `est_filament` per-tray rows, `fetch_state` note when not `done`, actual-grams input, Download 3MF link when `has_file`. Gate edits with `can("library:write")` and owner-or-admin from `useAuth()`.
- [ ] **Step 4: Run** → PASS; `npm run build && npx oxlint`.
- [ ] **Step 5: Commit** `git add -A web && ... commit -m "feat(web): archive-backed run history with grading sheet"`

### Task 11: Retire client-side `printCost.ts`

**Files:**
- Delete: `web/src/lib/printCost.ts`, `web/src/lib/printCost.test.ts`
- Modify: `web/src/components/model-detail/PrintsTab.tsx` (`entry.total_cost`), `web/src/components/model-detail/PlatePanel.tsx` (`plate.est_cost`, drop the `useAppSettings` read if now unused), `web/src/components/settings/PrintCostCard.tsx` (doc comment → server costing), their tests
- Test: `web/src/components/model-detail/PrintsTab.test.tsx`, `web/src/components/model-detail/PlatePanel.test.tsx`

**Interfaces:**
- Consumes: `PrintEntry.total_cost`, `PlateOut.est_cost`, `formatCost`.

- [ ] **Step 1: Update tests** to assert `Est. cost: 8.00` from `plate.est_cost: 8` and a print entry showing `8.00` from `total_cost: 8` with no app-settings mock.
- [ ] **Step 2: Run** `npx vitest run src/components/model-detail` → FAIL.
- [ ] **Step 3: Implement**; `grep -rn printCost web/src` returns nothing.
- [ ] **Step 4: Run** → PASS; `npm run build && npx oxlint && npx vitest run`.
- [ ] **Step 5: Commit** `... commit -m "refactor(web): use server-computed print costs"`

### Task 12: Docs + live-verify checklist

**Files:**
- Modify: `README.md` (new `### Print archive & run history` under `## Printer integration`: what is archived, external prints, cost formula + partial charging, Good/Reject, `{data_dir}/archives/` storage and backup note, reprint arrives in P5)
- Create: `docs/superpowers/plans/2026-10-21-p2-live-verify.md`

- [ ] **Step 1: Write the checklist file** with exactly:

```markdown
# P2 live-verify (A1 Mini)

- [ ] LAN send from the app: confirm `/cache/tdmm-<job_id>.gcode.3mf` exists after start (FTPS `LIST /cache`).
- [ ] Studio "Send" (not "Print"): record the stored name/extension (`/cache/<subtask>.3mf` vs `.gcode.3mf`, root vs `/cache`); fix `services/archive.py` candidates if different.
- [ ] Touchscreen reprint from SD: subtask_name reported and file location.
- [ ] `/cache` purge: is the file still present after the run finishes / after the next print / after reboot?
- [ ] Cancel from touchscreen and from Studio at ~20%: record `gcode_state` and `print_error`; confirm `CANCEL_ERROR_CODES` in `printerd.py`.
- [ ] Stop from the app: run result `stopped`, job `canceled`, partial grams ≈ est × layer/total.
- [ ] Completed small print: archive row has 3MF, thumbnail, grams, cost; run shows in Printer → history.
- [ ] printerd restart mid-print: no duplicate external job; archive still created on finish.
```

- [ ] **Step 2: Run** full gate (spec §Verification) incl. `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`.
- [ ] **Step 3: Commit** `... commit -m "docs: document print archive and add P2 live-verify checklist"`
