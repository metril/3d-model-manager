from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import UserRole


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
