"""The library scanner/reconciler (SPEC "Rescan/reconcile (`scan_library`)";
Task 5 brief).

**CRITICAL invariant (Global Constraints "SCANNER NEVER DELETES LIBRARY
FILES"):** this module only ever *reads* the backend (``walk``/``read``/
``stat``), touches ``verified_at``/``mtime``, repoints/inserts DB rows, and
marks files ``missing``. It NEVER calls ``backend.delete``/``backend.move``
on library content and NEVER deletes a ``files``/``models``/``revisions``
row. A DB path absent on disk is reported ``missing`` for a human to resolve
in the UI -- this module leaves it alone.

Runs entirely in the worker's SYNC world (see ``app.tasks.base``) -- callers
pass a plain SQLAlchemy ``Session``, never the API's async engine.

Reconcile algorithm (SPEC decision table, implemented exactly, in TWO
PASSES over the walk -- see ``run_scan``'s docstring / Task 5 fix-wave
Finding 1 for why a single pass isn't safe):

1. Snapshot ``{storage_path: File}`` (size comes from the joined ``Blob``).
2. Pass 1: ``backend.walk("")`` the tree once, skipping ``.3dmm.json``
   sidecars (handled separately below -- they aren't ``files`` rows). Per
   on-disk entry:
   - **Known path, unchanged** (size matches, and on local/SMB mtime matches
     within tolerance; S3 compares size only): touch ``verified_at``, count
     toward ``report["verified"]``. No re-hash.
   - **Known path, changed**: re-hash. Same hash -> just refresh
     ``mtime``/``verified_at``. New hash -> upsert the ``Blob``, repoint
     ``file.blob_hash``, append to ``report["changed"]``, best-effort
     re-enqueue the pipeline.
   - **Unknown path**: deferred to pass 2 (not yet hashed).
   Every known-path entry visited this pass is added to a ``seen`` set.
3. The CONFIRMED-missing set is computed only now, after the full walk:
   every snapshot row whose ``storage_path`` was never in ``seen``.
4. Pass 2, over the entries deferred in step 2: re-hash (``files_hashed``
   still only counts the actually-unknown paths, not the whole tree). Known
   hash with a matching row IN THE CONFIRMED-missing set (same hash +
   matching ``rel_path`` tail) -> relink (repoint ``storage_path``), and
   remove that row from the confirmed-missing set so it can't be claimed
   twice. Known hash with no such candidate, or unknown hash -> adopt
   (attach to an existing model+revision if the key fits
   ``<slug>/<dir_name>/<rel...>``, else create a draft ``Model`` +
   ``rev-001`` flagged ``review_state="adopted"``).
5. Whatever remains in the confirmed-missing set after pass 2 ->
   ``missing``. Its row and the (absent) object are both left untouched.
6. Counters + the report JSONB are written onto the ``ScanRun`` row, which is
   marked ``state="done"``.

A single unreadable file (``report["errors"]``: TOCTOU deletion between
``walk`` and ``read``, or a transient SMB/S3 read error) is recorded and
skipped rather than aborting the whole run -- see ``_record_error``.

**Multi-backend (Workstream C task C2):** ``run_scan_all_backends`` -- the
entrypoint ``app.tasks.scan.scan_library`` actually calls -- loops every
configured ``storage_backends`` row and runs the algorithm above ONCE PER
BACKEND via ``_reconcile_against_backend``, scoped to the ``files`` rows
whose ``backend_id`` matches that backend (default backend also covers any
NULL ``backend_id``, the pre-migration-seed safety net). Each backend's
``missing_candidates`` set is therefore built from ONLY that backend's own
files, so a file that simply lives on a *different* backend is never
reported missing just because it wasn't on THIS backend's walk; adopted and
relinked files are stamped with the currently-walked backend's id and get a
``file_locations`` row. ``run_scan`` itself keeps its original single-backend
signature (``backend`` passed in directly, no DB backend lookup) for
backward compatibility with direct callers/tests that inject a backend
object of their own (e.g. the synthetic in-memory harnesses in
``tests/test_scanner.py``) -- called with no backend scoping (the historical
behavior), it reconciles every ``files`` row unfiltered, same as before this
task.

Implementation decisions not spelled out verbatim by the SPEC table (documented
here rather than guessed silently, per the task's "stop on genuine ambiguity"
instruction -- none of these affect data-integrity, only report/adoption
bookkeeping for corner cases the table doesn't enumerate):

- ``report["verified"]`` is only bumped by the cheap size/mtime match (SPEC
  text literally scopes it to the "unchanged" row); a re-hash that lands back
  on the SAME hash (the "changed" row's first branch) updates ``mtime``/
  ``verified_at`` but isn't separately counted anywhere.
- The "adopted duplicate" branch (known hash, no relink candidate) and the
  "unknown hash" adopt branch share one attach-or-create-draft helper: SPEC
  spells out the fits-existing-model/else-create-draft split only for the
  unknown-hash case, but the same fallback is the only sane behavior for a
  duplicate that also doesn't fit anywhere.
- If attaching an adopted file would collide with an existing
  ``(revision_id, rel_path)`` row (a pre-existing DB inconsistency), it falls
  through to the create-draft-model branch instead of raising -- a single
  odd file must never abort the whole scan.
- The scanner also refreshes stale ``.3dmm.json`` sidecars it encounters
  (Global Constraints carried item: sidecars could go stale before Task 5's
  ``patch_model`` fix started keeping them in sync going forward) -- read
  back, compared against the model's current authoritative content, and
  rewritten only if they differ.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import PurePosixPath

from blake3 import blake3
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.config import Settings
from app.models import Blob, File, FileLocation, Model, Revision, ScanRun, StorageBackendRow
from app.services import layout
from app.services import prints as prints_service
from app.services import storage_backends as storage_backends_service
from app.storage.base import EntryInfo, StorageBackend
from app.storage.s3 import S3StorageBackend

logger = logging.getLogger(__name__)

# How close local/SMB's on-disk mtime must be to the last-known `File.mtime`
# to count as "unchanged" without a re-hash -- absorbs filesystem timestamp
# rounding, not a meaningful window for anything else.
_MTIME_TOLERANCE_S = 2.0

# Pass-2 chunk size (D2, M6 Task 6): unknown-path entries are reconciled
# `_CHUNK` at a time, each chunk's DB work batched into a small constant
# number of round trips and then committed -- a checkpoint, so a mid-scan
# crash keeps every earlier chunk's already-committed data rather than
# rolling the whole run back. A module-level name (not a local/parameter
# default) so tests can `monkeypatch.setattr(scanner, "_CHUNK", ...)` to
# exercise multi-chunk behavior against a small fake walk.
_CHUNK = 500


def mark_scan_state(session: Session, scan_run_id: int, state: str) -> None:
    """Transition a ``ScanRun``'s ``state`` (Celery task bookkeeping, called
    before/around ``run_scan`` -- ``run_scan`` itself sets the terminal
    ``"done"`` state as the last step of a successful pass).
    """
    scan_run = session.get(ScanRun, scan_run_id)
    if scan_run is None:
        raise LookupError(f"scan run {scan_run_id} not found")
    scan_run.state = state
    if state in ("failed", "skipped"):
        scan_run.finished_at = datetime.now(UTC)
    session.commit()


def run_scan(
    session: Session, settings: Settings, backend: StorageBackend, scan_run_id: int
) -> None:
    """The whole reconcile pass against ``scan_run_id`` (must already exist,
    created by the caller), against a single, CALLER-SUPPLIED backend.
    Synchronous end to end.

    Kept at this original (pre-Workstream-C) signature for backward
    compatibility with direct callers/tests that inject their own backend
    object (e.g. the synthetic in-memory harnesses in
    ``tests/test_scanner.py``) rather than one resolved from a
    ``storage_backends`` DB row -- ``settings`` is accepted but unused here
    for exactly that reason. Reconciles every ``files`` row UNFILTERED (no
    ``backend_id`` scoping, and adopted/relinked files are left with
    ``backend_id=NULL``), i.e. the historical single-backend behavior.
    ``run_scan_all_backends`` below is what ``app.tasks.scan.scan_library``
    actually calls -- it loops every configured backend and calls
    ``_reconcile_against_backend`` (this function's extracted body) once per
    backend, scoped, then finalizes the ``ScanRun`` row once at the end.

    See ``_reconcile_against_backend``'s docstring for the two-pass
    algorithm itself.
    """
    scan_run = session.get(ScanRun, scan_run_id)
    if scan_run is None:
        raise LookupError(f"scan run {scan_run_id} not found")

    now = datetime.now(UTC)
    counters = {"files_seen": 0, "files_hashed": 0, "relinked": 0, "adopted": 0}
    report: dict[str, object] = {
        "adopted": [],
        "relinked": [],
        "changed": [],
        "missing": [],
        "errors": [],
        "verified": 0,
    }

    _reconcile_against_backend(session, backend, None, now, counters, report)

    scan_run.files_seen = counters["files_seen"]
    scan_run.files_hashed = counters["files_hashed"]
    scan_run.relinked = counters["relinked"]
    scan_run.adopted = counters["adopted"]
    scan_run.missing = len(report["missing"])
    scan_run.report = report
    scan_run.state = "done"
    scan_run.finished_at = datetime.now(UTC)
    session.commit()


def run_scan_all_backends(session: Session, settings: Settings, scan_run_id: int) -> None:
    """The REAL entrypoint (Workstream C task C2): every configured
    ``storage_backends`` row gets its own full two-pass reconcile
    (``_reconcile_against_backend``), scoped to the ``files`` rows whose
    ``backend_id`` points at that row -- a file that simply lives on a
    DIFFERENT backend is never reported missing just because it wasn't on
    THIS backend's walk (each backend's ``missing_candidates`` is built from
    only its own files). Adopted/relinked files are stamped with the
    currently-walked backend's id and get a ``file_locations`` row.

    Self-heals a missing default backend row first (mirrors
    ``storage_backends.resolve_default_backend_sync``'s own self-heal): a
    scan must never find zero backends to walk (that would silently scan
    nothing and report every file missing).
    """
    scan_run = session.get(ScanRun, scan_run_id)
    if scan_run is None:
        raise LookupError(f"scan run {scan_run_id} not found")

    storage_backends_service.resolve_default_backend_sync(session, settings)
    backend_rows = storage_backends_service.list_backends_sync(session)

    now = datetime.now(UTC)
    counters = {"files_seen": 0, "files_hashed": 0, "relinked": 0, "adopted": 0}
    report: dict[str, object] = {
        "adopted": [],
        "relinked": [],
        "changed": [],
        "missing": [],
        "errors": [],
        "verified": 0,
    }

    for row in backend_rows:
        backend = storage_backends_service.backend_for_id_sync(session, settings, row.id)
        _reconcile_against_backend(session, backend, row, now, counters, report)

    scan_run.files_seen = counters["files_seen"]
    scan_run.files_hashed = counters["files_hashed"]
    scan_run.relinked = counters["relinked"]
    scan_run.adopted = counters["adopted"]
    scan_run.missing = len(report["missing"])
    scan_run.report = report
    scan_run.state = "done"
    scan_run.finished_at = datetime.now(UTC)
    session.commit()

    # R13b Risk resolution 4: self-heal any `Model.print_count` drift on
    # every scan, rather than trusting `app.services.prints`' single-writer
    # increments/decrements to never fall out of sync forever.
    prints_service.recount_print_counts_sync(session)


def _touch_location(session: Session, file_id: int, backend_id: int, verified_at: datetime) -> None:
    """Get-or-create the ``(file_id, backend_id)`` ``file_locations`` row and
    stamp its ``verified_at``: self-healing (a location row absent for a
    file this backend's walk just confirmed present -- e.g. a pre-Workstream-
    C write path, or a row lost to some earlier bug -- is created rather than
    left missing) as well as the normal "this backend's copy is still there"
    confirmation for a row that already exists.
    """
    loc = session.get(FileLocation, (file_id, backend_id))
    if loc is None:
        session.add(FileLocation(file_id=file_id, backend_id=backend_id, verified_at=verified_at))
    else:
        loc.verified_at = verified_at


def _reconcile_against_backend(
    session: Session,
    backend: StorageBackend,
    backend_row: StorageBackendRow | None,
    now: datetime,
    counters: dict[str, int],
    report: dict[str, object],
) -> None:
    """One backend's full two-pass reconcile, mutating ``counters``/``report``
    in place (the caller -- ``run_scan``/``run_scan_all_backends`` -- owns
    finalizing the ``ScanRun`` row, so more than one backend's counts can be
    accumulated before that happens).

    ``backend_row`` is ``None`` for ``run_scan``'s legacy single-backend
    callers (no ``backend_id`` scoping/stamping at all -- see that
    function's docstring) or the ``storage_backends`` row this pass is
    scoped to otherwise: its ``files`` snapshot only includes rows whose
    ``backend_id`` matches (plus NULL ``backend_id`` rows too, when this is
    the DEFAULT backend -- the pre-migration-seed safety net), and every
    known/adopted/relinked file this pass touches is stamped with
    ``backend_row.id`` plus a confirmed ``file_locations`` row.

    Runs in TWO PASSES over the walked tree (Task 5 fix-wave Finding 1):
    pass 1 resolves every on-disk entry that matches a known
    ``files.storage_path`` and defers every UNKNOWN entry; only once pass 1
    has walked the WHOLE tree do we know which snapshot rows were genuinely
    never seen on disk -- that ``missing_candidates`` set is the only pool
    pass 2's relink may draw from. The pre-fix single pass instead matched a
    relink candidate against *any not-yet-visited* row, including one whose
    own on-disk file was simply later in walk order (still present) -- with
    two byte-identical files sharing a basename in different models, moving
    one could falsely relink the other, silently corrupting a still-present
    file's ``storage_path`` while reporting the actually-moved file missing.

    Pass 2 itself runs in ``_CHUNK``-sized batches (D2, M6 Task 6): every
    adopt target it could need is preloaded ONCE, up front, via
    ``_preload_adopt_targets`` -- never mid-chunk -- and each chunk commits
    when it's done (a checkpoint: a mid-scan crash keeps every earlier
    chunk's data). None of this moves any work earlier than the full pass-1
    walk above; it only changes how pass 2's own DB round trips are batched.
    """
    backend_id = backend_row.id if backend_row is not None else None
    size_only = isinstance(backend, S3StorageBackend)

    files_stmt = select(File).options(joinedload(File.blob))
    if backend_row is not None:
        scope = File.backend_id == backend_row.id
        if backend_row.is_default:
            scope = or_(scope, File.backend_id.is_(None))
        files_stmt = files_stmt.where(scope)
    files_by_path: dict[str, File] = {
        f.storage_path: f for f in session.execute(files_stmt).unique().scalars()
    }

    # Replicated copies: a file whose PRIMARY backend is a different one but
    # whose bytes ALSO live here via a `file_locations` row (relocate mode
    # "replicate"). `backend.walk("")` returns those bytes too, so without
    # recognizing them they'd look like orphans and get adopted as a phantom
    # "adopted" duplicate model. Add them to `files_by_path` for RECOGNITION
    # only -- they must never enter `missing_candidates` (a missing replica
    # isn't a missing file) and their primary `backend_id` must not be
    # overwritten when seen. `replica_paths` marks them for both.
    replica_paths: set[str] = set()
    if backend_row is not None:
        replica_stmt = (
            select(File)
            .options(joinedload(File.blob))
            .join(FileLocation, FileLocation.file_id == File.id)
            .where(FileLocation.backend_id == backend_row.id)
        )
        for f in session.execute(replica_stmt).unique().scalars():
            if f.storage_path not in files_by_path:
                files_by_path[f.storage_path] = f
                replica_paths.add(f.storage_path)

    seen: set[str] = set()
    deferred: list[EntryInfo] = []

    # --- Pass 1: resolve every KNOWN path; defer every UNKNOWN one. --------
    for entry in backend.walk(""):
        if entry.key.startswith("backups/"):  # Q7: backup artifacts are not library files
            continue
        parts = entry.key.split("/")
        if len(parts) == 2 and parts[1] == layout.SIDECAR_NAME:
            _maybe_refresh_sidecar(session, backend, entry.key, parts[0])
            continue

        counters["files_seen"] += 1

        file = files_by_path.get(entry.key)
        if file is not None:
            _reconcile_known(session, backend, file, entry, size_only, now, counters, report)
            if backend_id is not None:
                # A replica seen on this backend: confirm its location, but
                # leave its primary `backend_id` on the OTHER backend untouched.
                if entry.key not in replica_paths:
                    file.backend_id = backend_id
                _touch_location(session, file.id, backend_id, now)
            seen.add(entry.key)
            continue

        deferred.append(entry)

    # The CONFIRMED-missing set: snapshot rows whose storage_path the full
    # walk never visited. Pass 2's relink may only claim rows out of this
    # set -- popped as each relink claims one, so the same origin row can
    # never be relinked twice in one scan.
    missing_candidates: dict[int, File] = {
        f.id: f
        for path, f in files_by_path.items()
        if path not in seen and path not in replica_paths
    }

    adopted_index: dict[tuple[int, int], dict] = {}
    # Top-level on-disk directory name -> the draft (Model, Revision) created
    # for it THIS scan, so multiple adopted files under the same dropped
    # folder land on one new model, not one model per file.
    draft_models: dict[str, tuple[Model, Revision]] = {}

    # --- Pass 2: resolve every UNKNOWN path against the confirmed-missing --
    # --- set only, in `_CHUNK`-sized batches (D2). One-shot preload of --
    # --- every adopt target the chunks could need (mirrors the --
    # --- `files_by_path` snapshot above), THEN chunk -- never before the --
    # --- full pass-1 walk above has computed `missing_candidates`. --------
    models_by_slug, revisions_by_key, taken = _preload_adopt_targets(session, deferred)
    for i in range(0, len(deferred), _CHUNK):
        _reconcile_unknown_chunk(
            session,
            backend,
            deferred[i : i + _CHUNK],
            missing_candidates,
            draft_models,
            models_by_slug,
            revisions_by_key,
            taken,
            adopted_index,
            now,
            counters,
            report,
            backend_id,
        )
        # Checkpoint: a crash partway through a large scan keeps every
        # already-processed chunk's data rather than rolling the whole run
        # back (D2). The caller (`scan_library`) still marks the ScanRun
        # `failed` if a later chunk raises.
        session.commit()

    missing_files = list(missing_candidates.values())
    slug_by_file_id = _slugs_for_files(session, [f.id for f in missing_files])
    for file in missing_files:
        report["missing"].append(
            {
                "file_id": file.id,
                "storage_path": file.storage_path,
                "model_slug": slug_by_file_id.get(file.id, "?"),
            }
        )


def _slugs_for_files(session: Session, file_ids: list[int]) -> dict[int, str]:
    if not file_ids:
        return {}
    rows = session.execute(
        select(File.id, Model.slug)
        .join(Revision, Revision.id == File.revision_id)
        .join(Model, Model.id == Revision.model_id)
        .where(File.id.in_(file_ids))
    ).all()
    return dict(rows)


def _hash_entry(backend: StorageBackend, key: str) -> str:
    hasher = blake3()
    for chunk in backend.read(key):
        hasher.update(chunk)
    return hasher.hexdigest()


def _record_error(report: dict[str, object], storage_path: str, exc: Exception) -> None:
    """Task 5 fix-wave Finding 3: a single unreadable file (TOCTOU deletion
    between ``walk`` and ``read``, or a transient SMB/S3 read error) must
    never abort the whole scan. Record it and let the caller move on.
    """
    report["errors"].append({"storage_path": storage_path, "error": str(exc)})
    logger.warning("scan: read failed for %s: %s", storage_path, exc, exc_info=True)


def _reconcile_known(
    session: Session,
    backend: StorageBackend,
    file: File,
    entry: EntryInfo,
    size_only: bool,
    now: datetime,
    counters: dict[str, int],
    report: dict[str, object],
) -> None:
    blob = file.blob
    unchanged = entry.size == blob.size and (
        size_only
        or (
            file.mtime is not None
            and abs((entry.mtime - file.mtime).total_seconds()) <= _MTIME_TOLERANCE_S
        )
    )
    if unchanged:
        file.verified_at = now
        report["verified"] += 1
        return

    counters["files_hashed"] += 1
    try:
        new_hash = _hash_entry(backend, entry.key)
    except Exception as exc:
        # The path IS present -- the walk found it -- so it must never be
        # reported `missing`; leave the row untouched and move on (Finding 3).
        _record_error(report, entry.key, exc)
        return
    if new_hash == file.blob_hash:
        file.mtime = entry.mtime
        file.verified_at = now
        return

    old_hash = file.blob_hash
    new_blob = session.get(Blob, new_hash)
    if new_blob is None:
        kind, format_ = layout.infer_blob_kind_format(file.rel_path)
        new_blob = Blob(hash=new_hash, size=entry.size, kind=kind, format=format_)
        session.add(new_blob)
        session.flush()

    file.blob_hash = new_hash
    file.mtime = entry.mtime
    file.verified_at = now
    report["changed"].append(
        {
            "file_id": file.id,
            "storage_path": entry.key,
            "old_hash": old_hash,
            "new_hash": new_hash,
        }
    )
    _best_effort_pipeline(session, blob_hash=new_hash, file_id=file.id)


def _find_relink_candidate(
    missing_candidates: dict[int, File], digest: str, key: str
) -> File | None:
    """Pick the confirmed-missing row (see ``run_scan``'s ``missing_candidates``
    -- rows the full first pass never matched on disk) this ``key`` should
    relink to: same blob hash, matching ``rel_path`` tail. Never called
    against a row whose own on-disk path might just be later in walk order
    (Task 5 fix-wave Finding 1) -- ``missing_candidates`` is only ever built
    from the FULL first pass's leftovers.
    """
    matches = sorted(
        (
            f
            for f in missing_candidates.values()
            if f.blob_hash == digest and (key == f.rel_path or key.endswith("/" + f.rel_path))
        ),
        key=lambda f: f.id,
    )
    return matches[0] if matches else None


def _unique_slug_sync(session: Session, name: str) -> str:
    base = layout.slug_for(name)
    slug = base
    suffix = 2
    while session.scalar(select(Model.id).where(Model.slug == slug)) is not None:
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def _preload_adopt_targets(
    session: Session, deferred: list[EntryInfo]
) -> tuple[dict[str, Model], dict[tuple[int, str], Revision], set[tuple[int, str]]]:
    """One-shot bulk load of every adopt target pass 2's chunks could need
    (D2, M6 Task 6), replacing the per-file ``select(Model)``/
    ``select(Revision)``/``_rel_path_taken`` round trips with in-memory
    dict/set lookups. Mirrors ``run_scan``'s ``files_by_path`` snapshot
    pattern -- called once, before any chunk runs, off the FULL ``deferred``
    list (never re-run mid-scan; ``_resolve_adopt_target`` keeps these
    structures current in-place as it creates draft models or attaches
    files during the chunk loop).
    """
    slugs = {p[0] for e in deferred if len(p := e.key.split("/")) >= 3}
    if not slugs:
        return {}, {}, set()

    models = list(session.execute(select(Model).where(Model.slug.in_(slugs))).scalars())
    models_by_slug = {m.slug: m for m in models}

    model_ids = [m.id for m in models]
    revisions_by_key: dict[tuple[int, str], Revision] = {}
    taken: set[tuple[int, str]] = set()
    if model_ids:
        for rev in session.execute(
            select(Revision).where(Revision.model_id.in_(model_ids))
        ).scalars():
            revisions_by_key[(rev.model_id, rev.dir_name)] = rev
        rev_ids = [r.id for r in revisions_by_key.values()]
        if rev_ids:
            for rid, rel in session.execute(
                select(File.revision_id, File.rel_path).where(File.revision_id.in_(rev_ids))
            ):
                taken.add((rid, rel))
    return models_by_slug, revisions_by_key, taken


def _resolve_adopt_target(
    session: Session,
    backend: StorageBackend,
    key: str,
    draft_models: dict[str, tuple[Model, Revision]],
    models_by_slug: dict[str, Model],
    revisions_by_key: dict[tuple[int, str], Revision],
    taken: set[tuple[int, str]],
) -> tuple[Model, Revision, str]:
    """Where an adopted ``key`` should attach: an existing model+revision if
    it fits ``<slug>/<dir_name>/<rel...>``, otherwise a draft model (reusing
    one already created this scan for the same top-level directory).

    Consults the preloaded ``models_by_slug``/``revisions_by_key``/``taken``
    (from ``_preload_adopt_targets``) instead of querying -- no DB round
    trip on the existing-model path at all. A genuinely new top-level
    folder still creates+caches a draft model (necessarily a DB round trip,
    but bounded by the number of distinct dropped folders, not file count),
    and registers it into ``models_by_slug``/``revisions_by_key`` so later
    files in the same scan (including a later chunk) see it without
    re-querying.
    """
    parts = key.split("/")
    if len(parts) >= 3:
        slug, dir_name, rel_path = parts[0], parts[1], "/".join(parts[2:])
        model = models_by_slug.get(slug)
        if model is not None:
            revision = revisions_by_key.get((model.id, dir_name))
            if revision is not None and (revision.id, rel_path) not in taken:
                return model, revision, rel_path

    top = parts[0] if len(parts) > 1 else PurePosixPath(parts[0]).stem
    rel_path = "/".join(parts[1:]) if len(parts) > 1 else parts[0]

    if top in draft_models:
        model, revision = draft_models[top]
        return model, revision, rel_path

    slug = _unique_slug_sync(session, top)
    model = Model(slug=slug, name=top, review_state="adopted")
    session.add(model)
    session.flush()
    dir_name = layout.revision_dir_name(1, "initial")
    revision = Revision(model_id=model.id, number=1, name="initial", dir_name=dir_name)
    session.add(revision)
    session.flush()
    model.current_revision_id = revision.id
    layout.write_sidecar(backend, model.id, slug, top)
    draft_models[top] = (model, revision)
    models_by_slug[slug] = model
    revisions_by_key[(model.id, dir_name)] = revision
    return model, revision, rel_path


def _record_adopted(
    report: dict[str, object],
    adopted_index: dict[tuple[int, int], dict],
    model: Model,
    revision: Revision,
    rel_path: str,
) -> None:
    key = (model.id, revision.id)
    entry = adopted_index.get(key)
    if entry is None:
        entry = {"model_id": model.id, "slug": model.slug, "revision_id": revision.id, "files": []}
        adopted_index[key] = entry
        report["adopted"].append(entry)
    entry["files"].append(rel_path)


def _attach_adopted_file(
    session: Session,
    backend: StorageBackend,
    entry: EntryInfo,
    blob_hash: str,
    draft_models: dict[str, tuple[Model, Revision]],
    models_by_slug: dict[str, Model],
    revisions_by_key: dict[tuple[int, str], Revision],
    taken: set[tuple[int, str]],
    adopted_index: dict[tuple[int, int], dict],
    now: datetime,
    counters: dict[str, int],
    report: dict[str, object],
    backend_id: int | None,
) -> File:
    """Resolve where ``entry`` attaches and stage its ``File`` row.

    Adds the row to the session but deliberately does NOT flush it (D2):
    the caller (``_reconcile_unknown_chunk``) batches ONE flush across every
    File/Blob row the whole chunk stages, so a chunk of N adopted files
    costs a small constant number of INSERT statements, not N. ``taken`` is
    updated immediately (a plain in-memory set, no DB round trip) so a
    later file in the SAME chunk sees this attachment right away -- mirrors
    the live-DB-visibility a per-file ``_rel_path_taken`` query used to give
    for free. ``backend_id`` (Workstream C task C2) is stamped onto the new
    row directly -- ``None`` for ``run_scan``'s legacy unscoped callers,
    otherwise the backend currently being walked; the caller adds this
    file's ``file_locations`` row once the chunk's flush has assigned it an
    id.
    """
    model, revision, rel_path = _resolve_adopt_target(
        session, backend, entry.key, draft_models, models_by_slug, revisions_by_key, taken
    )

    file = File(
        revision_id=revision.id,
        blob_hash=blob_hash,
        rel_path=rel_path,
        storage_path=entry.key,
        mtime=entry.mtime,
        verified_at=now,
        backend_id=backend_id,
    )
    session.add(file)
    taken.add((revision.id, rel_path))

    counters["adopted"] += 1
    _record_adopted(report, adopted_index, model, revision, rel_path)
    return file


def _reconcile_unknown_chunk(
    session: Session,
    backend: StorageBackend,
    chunk: list[EntryInfo],
    missing_candidates: dict[int, File],
    draft_models: dict[str, tuple[Model, Revision]],
    models_by_slug: dict[str, Model],
    revisions_by_key: dict[tuple[int, str], Revision],
    taken: set[tuple[int, str]],
    adopted_index: dict[tuple[int, int], dict],
    now: datetime,
    counters: dict[str, int],
    report: dict[str, object],
    backend_id: int | None,
) -> None:
    """Reconcile one ``_CHUNK``-sized slice of pass 2's deferred (unknown-
    path) entries (D2, M6 Task 6), batching what used to be a handful of DB
    round trips PER FILE (``session.get(Blob, digest)``, the adopt-target
    selects, a Blob insert-flush, a File insert-flush) into a small constant
    number per CHUNK: ONE ``select(Blob.hash)`` for the whole chunk's
    blob-existence check, then ONE flush for every new ``Blob``/``File`` row
    the chunk stages (the best-effort pipeline dispatch, which needs each
    new File's generated id, is deferred until after that flush).

    Entries are still decided ONE AT A TIME, in walk order, with EXACTLY the
    same relink-vs-adopt / new-blob-vs-known-hash logic the old per-file
    reconcile used: relink pops its claimed row out of ``missing_candidates``
    immediately so a later entry in this same chunk can't claim it twice,
    and a hash first seen partway through this chunk is tracked in
    ``known_hashes`` so a later duplicate in the SAME chunk correctly takes
    the "known hash" branch instead of creating a second ``Blob`` row for it
    -- exactly what a live ``session.get(Blob, digest)`` would have found.
    Only the *round trips* are batched; the *decisions* are unchanged.
    """
    hashed: list[tuple[EntryInfo, str]] = []
    for entry in chunk:
        counters["files_hashed"] += 1
        try:
            digest = _hash_entry(backend, entry.key)
        except Exception as exc:
            # Not counted as adopted/relinked/missing -- there's no row to
            # leave "as-is" for a never-adopted path; just skip it
            # (Finding 3).
            _record_error(report, entry.key, exc)
            continue
        hashed.append((entry, digest))

    if not hashed:
        return

    digests = {digest for _, digest in hashed}
    known_hashes: set[str] = set(
        session.execute(select(Blob.hash).where(Blob.hash.in_(digests))).scalars()
    )

    pending_dispatch: list[tuple[File, str]] = []
    pending_locations: list[File] = []

    for entry, digest in hashed:
        if digest in known_hashes:
            candidate = _find_relink_candidate(missing_candidates, digest, entry.key)
            if candidate is not None:
                old_path = candidate.storage_path
                candidate.storage_path = entry.key
                candidate.mtime = entry.mtime
                candidate.verified_at = now
                counters["relinked"] += 1
                report["relinked"].append(
                    {"file_id": candidate.id, "from": old_path, "to": entry.key, "hash": digest}
                )
                missing_candidates.pop(candidate.id, None)
                if backend_id is not None:
                    candidate.backend_id = backend_id
                    _touch_location(session, candidate.id, backend_id, now)
                continue

            file = _attach_adopted_file(
                session,
                backend,
                entry,
                digest,
                draft_models,
                models_by_slug,
                revisions_by_key,
                taken,
                adopted_index,
                now,
                counters,
                report,
                backend_id,
            )
            pending_locations.append(file)
            continue

        kind, format_ = layout.infer_blob_kind_format(entry.key)
        # Added immediately (not deferred to a post-loop add_all) so a
        # mid-loop autoflush -- e.g. `_resolve_adopt_target`'s
        # `_unique_slug_sync` select or its explicit flushes when a later
        # entry in this same chunk adopts into a NEW draft model -- sees
        # this Blob already in the session before it inserts a later File
        # that references it. Deferring the add let Postgres see a File
        # insert whose `blob_hash` pointed at a Blob that existed only in a
        # local list, tripping `fk_files_blob_hash_blobs` (C1).
        session.add(Blob(hash=digest, size=entry.size, kind=kind, format=format_))
        known_hashes.add(digest)
        file = _attach_adopted_file(
            session,
            backend,
            entry,
            digest,
            draft_models,
            models_by_slug,
            revisions_by_key,
            taken,
            adopted_index,
            now,
            counters,
            report,
            backend_id,
        )
        pending_dispatch.append((file, digest))
        pending_locations.append(file)

    session.flush()

    if backend_id is not None:
        session.add_all(
            [
                FileLocation(file_id=f.id, backend_id=backend_id, verified_at=now)
                for f in pending_locations
            ]
        )

    for file, digest in pending_dispatch:
        _best_effort_pipeline(session, blob_hash=digest, file_id=file.id)


def _best_effort_pipeline(session: Session, *, blob_hash: str, file_id: int) -> None:
    """Best-effort pipeline dispatch (Task 5 brief: "swallowing dispatch
    errors -- a scan covers many files"). ``app.tasks.pipeline`` is imported
    locally to avoid a module-level import cycle (mirrors
    ``app.services.library``'s same-reasoning local imports of
    ``app.tasks.pipeline``).
    """
    from app.tasks.pipeline import start_pipeline_sync

    try:
        start_pipeline_sync(session, blob_hash=blob_hash, file_id=file_id)
    except Exception:
        logger.warning(
            "scan: pipeline dispatch failed for blob %s (file %s)",
            blob_hash,
            file_id,
            exc_info=True,
        )


def _maybe_refresh_sidecar(session: Session, backend: StorageBackend, key: str, slug: str) -> None:
    model = session.execute(select(Model).where(Model.slug == slug)).scalar_one_or_none()
    if model is None:
        return
    expected = layout.sidecar_content(model.id, model.slug, model.name)
    try:
        current = json.loads(b"".join(backend.read(key)))
    except Exception:
        current = None
    if current != expected:
        layout.write_sidecar(backend, model.id, model.slug, model.name)
