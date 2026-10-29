"""Slicer profiles, print recipes, and sidecar health (P6 "slicer, profiles,
recipes and dispatch" plan, Task 4).

``slicer_status_router`` carries ``GET /slicer/health`` on its own small
router (prefix ``/slicer``) mounted on ``protected_router`` -- a plain
session-gated endpoint, deliberately NOT added to ``app.api.slicer``'s
``router``, which is bearer-token only (Bambu Studio post-processing
intake) and would otherwise make this endpoint carry the wrong auth plane.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import anyio
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    require_owner_or_admin,
    require_perm,
    require_session,
    visible_printer_ids,
)
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Printer, PrintRecipe, SlicerPipeline, SlicerProfile
from app.models.enums import Fanout, ProfileKind, ProfileSource
from app.schemas.slicing import (
    ProfileImportOut,
    ProfileIn,
    ProfileListOut,
    ProfileOut,
    ProfileUpdate,
    RecipeIn,
    RecipeOut,
    RecipeUpdate,
    SlicerHealthOut,
)
from app.services.print_plan import recipe_compatibility_reason, recipe_scope_reason
from app.services.profile_scope import scope_for_printer
from app.services.server_config import get_server_config
from app.services.slicer_client import (
    SlicerError,
    SlicerProtocolError,
    SlicerUnavailable,
    client_from_config,
)
from app.services.slicer_profiles import (
    ProfileImportError,
    apply_meta,
    derive_profile_meta,
    filter_for_models,
    parse_bundle,
    profile_in_scope,
    scope_mismatch_reason,
    upsert_profiles,
    usable_models,
)

# I7: a hand-entered or scripted bundle upload shouldn't be able to exhaust
# worker memory/disk -- a legitimate Bambu Studio BBL export is a few MB at
# most.
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024

router = APIRouter(tags=["profiles"])
slicer_status_router = APIRouter(prefix="/slicer", tags=["slicer"])


def _profile_out(profile: SlicerProfile, *, with_data: bool) -> ProfileOut:
    return ProfileOut(
        id=profile.id,
        name=profile.name,
        kind=profile.kind,
        printer_model=profile.printer_model,
        nozzle_mm=profile.nozzle_mm,
        source=profile.source,
        printer_model_key=profile.printer_model_key,
        compat=profile.compat,
        layer_height_mm=profile.layer_height_mm,
        cloud_source=profile.cloud_source,
        created_by_user_id=profile.created_by_user_id,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        data=profile.data if with_data else None,
    )


async def _get_profile_or_404(db: AsyncSession, profile_id: int) -> SlicerProfile:
    profile = await db.get(SlicerProfile, profile_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "profile not found")
    return profile


async def _get_recipe_or_404(db: AsyncSession, recipe_id: int) -> PrintRecipe:
    recipe = await db.get(PrintRecipe, recipe_id)
    if recipe is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "recipe not found")
    return recipe


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


def _ilike_escape(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def resolve_scope(
    db: AsyncSession,
    settings: Settings,
    ctx: AuthContext,
    printer_id: int | None,
    nozzle_mm: float | None,
) -> tuple[str | None, float | None, str | None]:
    """`(printer_key, nozzle, nozzle_source)` for the list endpoints. Without a
    printer only an explicit nozzle override scopes anything."""
    if printer_id is None:
        return None, nozzle_mm, ("override" if nozzle_mm is not None else None)
    visible = visible_printer_ids(ctx)
    if visible is not None and printer_id not in visible:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "printer not found")
    client = aioredis.Redis.from_url(settings.redis_url)
    try:
        return await scope_for_printer(db, client, printer_id, nozzle_mm)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "printer not found") from None
    finally:
        await client.aclose()


async def _derive_meta(
    db: AsyncSession,
    kind: ProfileKind,
    name: str,
    data: dict,
    printer_model: str | None,
    nozzle_mm: float | None,
):
    """Scope metadata for an API-written profile; `inherits` parents and
    `compatible_printers` entries resolve against local rows."""
    refs = {str(data["inherits"])} if data.get("inherits") else set()
    refs |= {str(x) for x in data.get("compatible_printers") or [] if isinstance(x, str)}
    by_kind_name: dict[tuple[ProfileKind, str], SlicerProfile] = {}
    if refs:
        found = (
            (
                await db.execute(
                    select(SlicerProfile).where(
                        SlicerProfile.name.in_(refs),
                        SlicerProfile.kind.in_([kind, ProfileKind.PRINTER]),
                    )
                )
            )
            .scalars()
            .all()
        )
        by_kind_name = {(r.kind, r.name): r for r in found}

    def parent_lookup(pname: str):
        from app.services.slicer_profiles import compat_entries

        row = by_kind_name.get((kind, pname))
        return compat_entries(row) if row is not None else None

    def machine_lookup(mname: str):
        row = by_kind_name.get((ProfileKind.PRINTER, mname))
        return (row.printer_model, row.nozzle_mm) if row is not None else None

    return derive_profile_meta(
        kind,
        data,
        name=name,
        parent_lookup=parent_lookup,
        machine_lookup=machine_lookup,
        printer_model=printer_model,
        nozzle_mm=nozzle_mm,
    )


@router.get("/profiles", response_model=ProfileListOut)
async def list_profiles(
    kind: ProfileKind | None = None,
    printer_model: str | None = None,
    full: bool = False,
    printer_id: int | None = None,
    nozzle_mm: float | None = None,
    layer_height_mm: float | None = None,
    q: str | None = None,
    show_all: bool = False,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> ProfileListOut:
    """Profiles, optionally scoped to a printer (+ nozzle, + layer height for
    process profiles). `total` counts rows matching `kind`/`q` before scope
    filtering; `show_all=1` keeps out-of-scope rows, flagged `in_scope=false`
    with a `scope_reason`."""
    stmt = select(SlicerProfile).order_by(SlicerProfile.kind, SlicerProfile.name)
    if kind is not None:
        stmt = stmt.where(SlicerProfile.kind == kind)
    if printer_model is not None:
        stmt = stmt.where(SlicerProfile.printer_model == printer_model)
    if q and q.strip():
        stmt = stmt.where(SlicerProfile.name.ilike(_ilike_escape(q.strip()), escape="\\"))
    rows = (await db.execute(stmt)).scalars().all()
    total = len(rows)

    printer_key, nozzle, source = await resolve_scope(db, settings, ctx, printer_id, nozzle_mm)
    scoped = printer_id is not None or nozzle_mm is not None or layer_height_mm is not None
    items: list[ProfileOut] = []
    for row in rows:
        out = _profile_out(row, with_data=full)
        if scoped:
            ok = profile_in_scope(row, printer_key, nozzle, layer_height_mm)
            if not ok and not show_all:
                continue
            out.in_scope = ok
            if not ok:
                out.scope_reason = scope_mismatch_reason(row, printer_key, nozzle) or (
                    "layer height differs"
                )
        items.append(out)
    return ProfileListOut(
        items=items,
        total=total,
        nozzle_source=source,
        nozzle_mm=nozzle if scoped else None,
    )


@router.get("/profiles/{profile_id}", response_model=ProfileOut)
async def get_profile(profile_id: int, db: AsyncSession = Depends(get_db)) -> ProfileOut:
    profile = await _get_profile_or_404(db, profile_id)
    return _profile_out(profile, with_data=True)


@router.post(
    "/profiles",
    status_code=status.HTTP_201_CREATED,
    response_model=ProfileOut,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def create_profile(
    payload: ProfileIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> ProfileOut:
    profile = SlicerProfile(
        name=payload.name,
        kind=payload.kind,
        printer_model=payload.printer_model,
        nozzle_mm=payload.nozzle_mm,
        data=payload.data,
        source=ProfileSource.BUILTIN,
        created_by_user_id=ctx.user.id,
    )
    apply_meta(
        profile,
        await _derive_meta(
            db,
            payload.kind,
            payload.name,
            payload.data,
            payload.printer_model,
            payload.nozzle_mm,
        ),
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return _profile_out(profile, with_data=True)


@router.patch(
    "/profiles/{profile_id}",
    response_model=ProfileOut,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def update_profile(
    profile_id: int,
    payload: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> ProfileOut:
    profile = await _get_profile_or_404(db, profile_id)
    require_owner_or_admin(ctx, profile.created_by_user_id)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(profile, field, value)
    if changes.keys() & {"name", "data", "printer_model", "nozzle_mm"}:
        apply_meta(
            profile,
            await _derive_meta(
                db,
                profile.kind,
                profile.name,
                profile.data,
                profile.printer_model,
                profile.nozzle_mm,
            ),
        )
    if changes and profile.source is ProfileSource.SYNCED:
        profile.source = ProfileSource.BUILTIN  # user-edited: a re-sync must not overwrite it
    await db.commit()
    await db.refresh(profile)
    return _profile_out(profile, with_data=True)


@router.delete(
    "/profiles/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def delete_profile(
    profile_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> None:
    profile = await _get_profile_or_404(db, profile_id)
    require_owner_or_admin(ctx, profile.created_by_user_id)

    recipes = (await db.execute(select(PrintRecipe))).scalars().all()
    for recipe in recipes:
        if (
            recipe.printer_profile_id == profile_id
            or recipe.process_profile_id == profile_id
            or profile_id in (recipe.filament_profile_ids or [])
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"profile is used by recipe {recipe.name!r}"
            )

    await db.delete(profile)
    await db.commit()


@router.post(
    "/profiles/import",
    response_model=ProfileImportOut,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def import_profiles(
    request: Request,
    printer_model: str | None = None,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> ProfileImportOut:
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("multipart/form-data"):
        content_length = request.headers.get("content-length")
        if content_length is not None and int(content_length) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"upload exceeds the {_MAX_UPLOAD_BYTES} byte limit",
            )
        form = await request.form()
        bundle = form.get("bundle")
        if bundle is None or not hasattr(bundle, "read"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "multipart upload must include a 'bundle' file",
            )
        data = await bundle.read()
        if len(data) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"upload exceeds the {_MAX_UPLOAD_BYTES} byte limit",
            )
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "bundle.zip"
            zip_path.write_bytes(data)
            result = await _import_from_path(db, zip_path, printer_model, ctx.user.id)
        return result

    try:
        payload: dict[str, Any] = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"invalid JSON: {exc}") from None

    if payload.get("from_sidecar"):
        cfg = await get_server_config(db, get_settings())
        if not cfg.slicer_enabled:
            raise HTTPException(status.HTTP_409_CONFLICT, "slicer_disabled")
        client = client_from_config(cfg)
        if client is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no slicer sidecar configured")
        try:
            profiles = await anyio.to_thread.run_sync(lambda: client.profiles(flatten=True))
        except SlicerUnavailable as exc:  # unreachable or busy
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
        except SlicerError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
        if profiles is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "slicer sidecar doesn't expose its own profiles"
            )
        if not payload.get("all"):
            models = (await db.execute(select(Printer.model))).scalars().all()
            wanted = usable_models(list(models))
            profiles = filter_for_models(profiles, wanted)
            if wanted and not any(p.get("kind") == "printer" for p in profiles):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "No sidecar profiles match your printer models ("
                    + ", ".join(sorted(set(wanted)))
                    + "). Tick 'All Bambu printers' to sync everything.",
                )
        return await _import_from_dicts(db, profiles, ctx.user.id, source=ProfileSource.SYNCED)

    path = payload.get("path")
    if path:
        if not ctx.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "path import is admin-only")
        bundle_path = Path(path)
        if not (bundle_path / "BBL.json").is_file():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"{path!r} has no BBL.json manifest"
            )
        return await _import_from_path(db, bundle_path, printer_model, ctx.user.id)

    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "expected a multipart 'bundle' file, or a JSON {path} or {from_sidecar: true} body",
    )


async def _import_from_path(
    db: AsyncSession, bundle_path: Path, printer_model: str | None, user_id: int | None
) -> ProfileImportOut:
    try:
        parsed = await anyio.to_thread.run_sync(parse_bundle, bundle_path, printer_model)
    except ProfileImportError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None

    from app.tasks import base

    def _run() -> dict:
        with base.sync_session() as session:
            return upsert_profiles(session, parsed, user_id)

    result = await anyio.to_thread.run_sync(_run)
    await db.commit()  # release this request's own txn before the caller sees fresh rows
    return ProfileImportOut(**result)


async def _import_from_dicts(
    db: AsyncSession,
    raw_profiles: list[dict],
    user_id: int | None,
    source: ProfileSource | None = None,
) -> ProfileImportOut:
    """Imports the sidecar's `/profiles?flatten=1` items (``kind``/``name``/
    ``printer_model``/``nozzle_mm``/``data``), skipping malformed ones.
    ``source=SYNCED`` makes it a non-destructive sync (see ``upsert_profiles``)."""
    from app.services.slicer_profiles import ParsedProfile

    parsed = []
    for raw in raw_profiles:
        try:
            kind = ProfileKind(raw["kind"])
            name = str(raw["name"])
        except (KeyError, ValueError):
            continue
        parsed.append(
            ParsedProfile(
                kind=kind,
                name=name,
                printer_model=raw.get("printer_model"),
                nozzle_mm=raw.get("nozzle_mm"),
                data=raw.get("data", raw),
            )
        )

    from app.tasks import base

    def _run() -> dict:
        with base.sync_session() as session:
            return upsert_profiles(session, parsed, user_id, source)

    result = await anyio.to_thread.run_sync(_run)
    await db.commit()
    return ProfileImportOut(**result)


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------


async def _validate_recipe(
    db: AsyncSession, ctx: AuthContext, data: dict[str, Any], *, existing: PrintRecipe | None
) -> None:
    merged = {
        "printer_profile_id": existing.printer_profile_id if existing else None,
        "process_profile_id": existing.process_profile_id if existing else None,
        "filament_profile_ids": existing.filament_profile_ids if existing else [],
        "fanout": existing.fanout if existing else Fanout.SINGLE,
        "n": existing.n if existing else None,
        "target_printer_id": existing.target_printer_id if existing else None,
    }
    merged.update({k: v for k, v in data.items() if k in merged})

    printer_profile = await db.get(SlicerProfile, merged["printer_profile_id"])
    if printer_profile is None or printer_profile.kind != ProfileKind.PRINTER:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "printer_profile_id must be a kind=printer profile",
        )

    process_profile = await db.get(SlicerProfile, merged["process_profile_id"])
    if process_profile is None or process_profile.kind != ProfileKind.PROCESS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "process_profile_id must be a kind=process profile",
        )

    for filament_id in merged["filament_profile_ids"] or []:
        if filament_id is None:
            continue
        filament_profile = await db.get(SlicerProfile, filament_id)
        if filament_profile is None or filament_profile.kind != ProfileKind.FILAMENT:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"filament_profile_ids[{filament_id}] must be a kind=filament profile",
            )

    if merged["fanout"] == Fanout.FIRST_N and not (merged["n"] and merged["n"] >= 1):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "fanout=first_n requires n >= 1")

    target_printer_id = merged["target_printer_id"]
    if target_printer_id is not None:
        printer = await db.get(Printer, target_printer_id)
        if printer is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "target printer not found")
        ctx.require_printer(target_printer_id, "print")


@router.get("/recipes", response_model=list[RecipeOut])
async def list_recipes(
    printer_id: int | None = None,
    nozzle_mm: float | None = None,
    layer_height_mm: float | None = None,
    compatible_only: bool = False,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> list[RecipeOut]:
    stmt = select(PrintRecipe).order_by(PrintRecipe.name)
    if q and q.strip():
        stmt = stmt.where(PrintRecipe.name.ilike(_ilike_escape(q.strip()), escape="\\"))
    rows = (await db.execute(stmt)).scalars().all()
    out = [RecipeOut.model_validate(row, from_attributes=True) for row in rows]
    scoped = printer_id is not None or nozzle_mm is not None or layer_height_mm is not None
    if not scoped:
        return out
    # resolve_scope first: an invisible printer is a 404 just like a missing one.
    printer_key, nozzle, _source = await resolve_scope(db, settings, ctx, printer_id, nozzle_mm)
    printer = None
    if printer_id is not None:
        printer = await db.get(Printer, printer_id)
        if printer is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "printer not found")
        ctx.require_printer(printer_id, "print")
    profiles = {p.id: p for p in (await db.execute(select(SlicerProfile))).scalars().all()}
    names = {pid: name for pid, name in (await db.execute(select(Printer.id, Printer.name))).all()}
    kept: list[RecipeOut] = []
    for recipe, item in zip(rows, out, strict=True):
        reason = None
        if printer is not None:
            printer_profile = profiles.get(recipe.printer_profile_id)
            reason = recipe_compatibility_reason(
                recipe,
                printer_profile.printer_model if printer_profile else None,
                printer,
                names.get(recipe.target_printer_id),
            )
        reason = reason or recipe_scope_reason(
            recipe, printer_key, nozzle, profiles, layer_height_mm
        )
        item.compatible = reason is None
        item.incompatible_reason = reason
        if reason is None or not compatible_only:
            kept.append(item)
    return kept


@router.get("/recipes/{recipe_id}", response_model=RecipeOut)
async def get_recipe(recipe_id: int, db: AsyncSession = Depends(get_db)) -> RecipeOut:
    recipe = await _get_recipe_or_404(db, recipe_id)
    return RecipeOut.model_validate(recipe, from_attributes=True)


@router.post(
    "/recipes",
    status_code=status.HTTP_201_CREATED,
    response_model=RecipeOut,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def create_recipe(
    payload: RecipeIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> RecipeOut:
    data = payload.model_dump()
    await _validate_recipe(db, ctx, data, existing=None)
    recipe = PrintRecipe(**data, created_by_user_id=ctx.user.id)
    db.add(recipe)
    await db.commit()
    await db.refresh(recipe)
    return RecipeOut.model_validate(recipe, from_attributes=True)


@router.patch(
    "/recipes/{recipe_id}",
    response_model=RecipeOut,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def update_recipe(
    recipe_id: int,
    payload: RecipeUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> RecipeOut:
    recipe = await _get_recipe_or_404(db, recipe_id)
    require_owner_or_admin(ctx, recipe.created_by_user_id)
    changes = payload.model_dump(exclude_unset=True)
    await _validate_recipe(db, ctx, changes, existing=recipe)
    for field, value in changes.items():
        setattr(recipe, field, value)
    await db.commit()
    await db.refresh(recipe)
    return RecipeOut.model_validate(recipe, from_attributes=True)


@router.delete(
    "/recipes/{recipe_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def delete_recipe(
    recipe_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> None:
    recipe = await _get_recipe_or_404(db, recipe_id)
    require_owner_or_admin(ctx, recipe.created_by_user_id)
    used_by = await db.scalar(
        select(SlicerPipeline.name).where(SlicerPipeline.recipe_id == recipe_id).limit(1)
    )
    if used_by is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"recipe is used by pipeline {used_by!r}")
    await db.delete(recipe)
    await db.commit()


# ---------------------------------------------------------------------------
# Sidecar health
# ---------------------------------------------------------------------------


@slicer_status_router.get("/health", response_model=SlicerHealthOut)
async def slicer_health(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> SlicerHealthOut:
    cfg = await get_server_config(db, settings)
    client = client_from_config(cfg)
    if client is None:
        return SlicerHealthOut(configured=False, ok=False)

    try:
        # M4: `abandon_on_cancel=True` lets `fail_after`'s timeout actually
        # give up waiting on the worker thread instead of blocking until
        # that thread call returns on its own -- the thread itself may
        # still run to completion in the background, but this request no
        # longer waits on it.
        with anyio.fail_after(3.0):
            result = await anyio.to_thread.run_sync(client.health, abandon_on_cancel=True)
    except TimeoutError:
        return SlicerHealthOut(configured=True, ok=False, error="timed out")
    except SlicerProtocolError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    except SlicerError as exc:
        return SlicerHealthOut(configured=True, ok=False, error=str(exc))

    return SlicerHealthOut(
        configured=True,
        ok=result["ok"],
        version=result["version"] or None,
        busy=result["busy"],
        running=result["running"],
        max_concurrency=result["max_concurrency"],
        job_timeout_s=result["job_timeout_s"],
        mem_limit=result["mem_limit"],
        bambu_version=result["bambu_version"],
    )
