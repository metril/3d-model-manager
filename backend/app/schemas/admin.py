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


_SLUG_RE = r"^[a-z0-9][a-z0-9-]{0,62}$"


def _https(v: str) -> str:
    if not v.startswith("https://"):
        raise ValueError("discovery_url must start with https://")
    return v


class OidcProviderOut(BaseModel):
    id: int
    name: str
    slug: str
    discovery_url: str
    client_id: str
    has_client_secret: bool
    scopes: str
    groups_claim: str
    group_mapping: dict[str, int]
    admin_group: str | None
    auto_create_users: bool
    link_by_email: bool
    enabled: bool
    callback_url: str


class OidcProviderCreateIn(BaseModel):
    name: str = Field(min_length=1)
    slug: str = Field(pattern=_SLUG_RE)
    discovery_url: str
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)
    scopes: str = "openid profile email groups"
    groups_claim: str = "groups"
    group_mapping: dict[str, int] = Field(default_factory=dict)
    admin_group: str | None = None
    auto_create_users: bool = False
    link_by_email: bool = False
    enabled: bool = True

    _v_url = field_validator("discovery_url")(_https)


class OidcProviderPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    slug: str | None = Field(default=None, pattern=_SLUG_RE)
    discovery_url: str | None = None
    client_id: str | None = Field(default=None, min_length=1)
    client_secret: str | None = Field(default=None, min_length=1)
    scopes: str | None = None
    groups_claim: str | None = None
    group_mapping: dict[str, int] | None = None
    admin_group: str | None = None
    auto_create_users: bool | None = None
    link_by_email: bool | None = None
    enabled: bool | None = None

    @field_validator("discovery_url")
    @classmethod
    def _v_url(cls, v: str | None) -> str | None:
        return None if v is None else _https(v)
