from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.enums import MembershipSource, UserRole
from app.services.permissions import validate_perms


class GroupRef(BaseModel):
    id: int
    name: str


class UserAdminOut(BaseModel):
    id: int
    username: str
    display_name: str | None
    email: str | None
    role: UserRole
    is_active: bool
    has_password: bool
    oidc_linked: bool
    last_login_at: datetime | None
    groups: list[GroupRef]


class UserCreateIn(BaseModel):
    username: str = Field(min_length=1)
    password: str | None = Field(default=None, min_length=8)
    display_name: str | None = None
    email: str | None = None
    role: UserRole = UserRole.MEMBER


class UserPatchIn(BaseModel):
    display_name: str | None = None
    email: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None


class PasswordResetIn(BaseModel):
    new_password: str = Field(min_length=8)


class GroupMemberOut(BaseModel):
    user_id: int
    username: str
    source: MembershipSource


class PrinterAccessIO(BaseModel):
    printer_id: int
    can_print: bool = False
    can_control: bool = False
    requires_review: bool = False


class GroupOut(BaseModel):
    id: int
    name: str
    description: str | None
    permissions: list[str]
    members: list[GroupMemberOut]
    printer_access: list[PrinterAccessIO]


def _perms(v: list[str] | None) -> list[str] | None:
    return None if v is None else validate_perms(v)


class GroupCreateIn(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None
    permissions: list[str] = []

    _check = field_validator("permissions")(_perms)


class GroupPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    permissions: list[str] | None = None

    _check = field_validator("permissions")(_perms)


class GroupMembersIn(BaseModel):
    user_ids: list[int]
