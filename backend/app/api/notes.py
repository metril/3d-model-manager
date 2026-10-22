"""Notes: model-level (``revision_id`` NULL) and per-revision (SPEC "API
surface", Task 5 brief). Included inline in model/revision GETs; this
router only carries the create/patch/delete verbs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_owner_or_admin, require_perm, require_session
from app.db import get_db
from app.models.library import Note
from app.schemas.library import NoteCreate, NoteOut, NotePatch
from app.services import library

router = APIRouter(prefix="/notes", tags=["notes"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=NoteOut,
    dependencies=[Depends(require_perm("library:write"))],
)
async def create_note(
    payload: NoteCreate,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> NoteOut:
    return await library.create_note(
        db,
        model_id=payload.model_id,
        revision_id=payload.revision_id,
        body=payload.body,
        created_by_user_id=ctx.user.id,
    )


@router.patch(
    "/{note_id}", response_model=NoteOut, dependencies=[Depends(require_perm("library:write"))]
)
async def patch_note(
    note_id: int, payload: NotePatch, db: AsyncSession = Depends(get_db)
) -> NoteOut:
    return await library.patch_note(db, note_id, payload.body)


@router.delete(
    "/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("library:write"))],
)
async def delete_note(
    note_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> None:
    note = await db.get(Note, note_id)
    if note is not None:  # unknown id falls through to the service's 404
        require_owner_or_admin(ctx, note.created_by_user_id)
    await library.delete_note(db, note_id)
