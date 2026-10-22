"""Duplicate-files report (Branch 4 Task 1): files sharing a blob hash
across more than one model -- surfaces reclaimable storage from the same
content having been imported/uploaded more than once.

Grouped over EVERY ``files`` row (every revision of every model, not just
current revisions) -- every row is bytes actually sitting on a storage
backend, so every row counts toward "wasted" storage regardless of which
revision it belongs to.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.library import Blob, File, Model, Revision
from app.schemas.reports import (
    DuplicateFileOut,
    DuplicateGroupOut,
    DuplicatesReport,
    DuplicatesResolveOut,
    KeepChoiceIn,
    SkippedCopyOut,
)
from app.services.library import try_delete_duplicate_copy


@dataclass(slots=True)
class _Bucket:
    size: int
    model_ids: set[int] = field(default_factory=set)
    files: list[DuplicateFileOut] = field(default_factory=list)


async def duplicate_files_report(db: AsyncSession) -> DuplicatesReport:
    stmt = (
        select(
            File.id,
            File.rel_path,
            File.blob_hash,
            Blob.size,
            Model.id,
            Model.slug,
            Model.name,
            Model.is_archived,
            Revision.id,
            Model.current_revision_id,
        )
        .join(Blob, Blob.hash == File.blob_hash)
        .join(Revision, Revision.id == File.revision_id)
        .join(Model, Model.id == Revision.model_id)
        .where(File.blob_hash.is_not(None))
    )
    rows = (await db.execute(stmt)).all()

    buckets: dict[str, _Bucket] = {}
    for (
        file_id,
        rel_path,
        blob_hash,
        size,
        model_id,
        model_slug,
        model_name,
        is_archived,
        revision_id,
        current_revision_id,
    ) in rows:
        bucket = buckets.setdefault(blob_hash, _Bucket(size=size))
        bucket.model_ids.add(model_id)
        bucket.files.append(
            DuplicateFileOut(
                model_id=model_id,
                model_slug=model_slug,
                model_name=model_name,
                model_archived=is_archived,
                file_id=file_id,
                file_name=rel_path,
                is_current_revision=revision_id == current_revision_id,
            )
        )

    groups = [
        DuplicateGroupOut(
            blob_hash=blob_hash,
            size=bucket.size,
            wasted_bytes=bucket.size * (len(bucket.files) - 1),
            files=sorted(bucket.files, key=lambda f: (f.model_id, f.file_id)),
        )
        for blob_hash, bucket in buckets.items()
        if len(bucket.model_ids) > 1
    ]
    groups.sort(key=lambda g: (-g.wasted_bytes, g.blob_hash))

    return DuplicatesReport(groups=groups, total_wasted_bytes=sum(g.wasted_bytes for g in groups))


async def resolve_duplicates(
    db: AsyncSession,
    settings: Settings,
    *,
    keep: list[KeepChoiceIn],
    authorize_models: Callable[[set[int]], Awaitable[None]] | None = None,
) -> DuplicatesResolveOut:
    """``POST /reports/duplicates/resolve`` (Round 11 Task 2): delete every
    copy in each named group EXCEPT the client's chosen keeper.

    Recomputes ``duplicate_files_report`` server-side rather than trusting
    the client's rows -- the report can have moved since the client fetched
    it (another delete, another upload). Every choice is validated against
    that fresh report BEFORE anything is deleted, so an invalid request
    (unknown group, or a keeper that isn't actually in its group) 404s with
    nothing touched -- same validate-then-mutate posture as
    ``bulk_hard_delete_models``. Groups NOT named in ``keep`` are left
    alone; the request is explicitly scoped to what it lists.

    Per-copy deletes go through ``try_delete_duplicate_copy``, which commits
    per file -- so a mid-loop storage failure on one copy leaves every
    already-deleted copy deleted (intended: the caller sees a partial
    ``deleted``/``skipped`` split rather than losing progress to a rollback).

    The keeper is re-verified to still exist right before its group's
    non-keepers are deleted: a concurrent ``DELETE /files/{id}`` landing on
    the keeper between the snapshot and this group's turn would otherwise
    let the loop delete every remaining copy of the blob's content. A
    vanished keeper skips the whole group -- its deletable copies with
    reason ``keeper_missing``, its old-revision ones keeping their own
    ``not_current_revision`` (they were never deletable to begin with, and
    the UI counts the two differently).
    """
    report = await duplicate_files_report(db)
    groups_by_hash = {group.blob_hash: group for group in report.groups}

    unknown_hashes = sorted(
        {choice.blob_hash for choice in keep if choice.blob_hash not in groups_by_hash}
    )
    if unknown_hashes:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"duplicate group(s) not found: {', '.join(unknown_hashes)}",
        )

    for choice in keep:
        group = groups_by_hash[choice.blob_hash]
        member_ids = {f.file_id for f in group.files}
        if choice.file_id not in member_ids:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"file {choice.file_id} is not part of duplicate group {choice.blob_hash}",
            )

    if authorize_models is not None:
        # Whole request 403s before any deletion (like bulk delete).
        victim_models = {
            entry.model_id
            for choice in keep
            for entry in groups_by_hash[choice.blob_hash].files
            if entry.file_id != choice.file_id
        }
        await authorize_models(victim_models)

    deleted = 0
    reclaimed_bytes = 0
    skipped: list[SkippedCopyOut] = []
    for choice in keep:
        group = groups_by_hash[choice.blob_hash]
        keeper_row = (
            await db.execute(select(File.id).where(File.id == choice.file_id))
        ).scalar_one_or_none()
        if keeper_row is None:
            # Old-revision copies keep their OWN reason even here: they were
            # never deletable in the first place (and the UI, which excludes
            # them from the count it promises, filters that reason out of its
            # "skipped" warning -- calling them keeper_missing would inflate
            # the warning past the number of copies the user was promised).
            skipped.extend(
                SkippedCopyOut(
                    file_id=entry.file_id,
                    reason="keeper_missing"
                    if entry.is_current_revision
                    else "not_current_revision",
                )
                for entry in group.files
                if entry.file_id != choice.file_id
            )
            continue
        for entry in group.files:
            if entry.file_id == choice.file_id:
                continue  # the keeper -- never a delete candidate
            reason = await try_delete_duplicate_copy(db, settings, entry.file_id)
            if reason is None:
                deleted += 1
                reclaimed_bytes += group.size
            else:
                skipped.append(SkippedCopyOut(file_id=entry.file_id, reason=reason))

    return DuplicatesResolveOut(deleted=deleted, reclaimed_bytes=reclaimed_bytes, skipped=skipped)
