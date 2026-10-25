"""``/settings/security``: instance-wide sign-in policy (T6 ``require_2fa``)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm
from app.db import get_db
from app.models import AuditLog
from app.services import two_factor

# GET is open to any session (the enrolment screen reads it); PUT is admin.
router = APIRouter(prefix="/settings/security", tags=["settings"])
_admin = Depends(require_perm("admin"))


class SecuritySettings(BaseModel):
    require_2fa: bool = False


@router.get("", response_model=SecuritySettings)
async def get_security(db: AsyncSession = Depends(get_db)) -> SecuritySettings:
    return SecuritySettings(require_2fa=await two_factor.require_2fa_enabled(db))


@router.put("", response_model=SecuritySettings)
async def put_security(
    payload: SecuritySettings,
    ctx: AuthContext = _admin,
    db: AsyncSession = Depends(get_db),
) -> SecuritySettings:
    old = await two_factor.require_2fa_enabled(db)
    await two_factor.set_require_2fa(db, payload.require_2fa)
    if old != payload.require_2fa:
        db.add(
            AuditLog(
                user_id=ctx.user.id,
                created_by_user_id=ctx.user.id,
                action="security_settings.update",
                payload={"changed": {"require_2fa": [old, payload.require_2fa]}},
            )
        )
    await db.commit()
    return payload
