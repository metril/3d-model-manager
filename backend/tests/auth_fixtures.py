"""Multi-user auth fixtures: user/group factories and per-user clients."""

import itertools
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import create_app
from app.models import Group, GroupMember, Printer, PrinterAccess, User
from app.models.enums import PrinterKind, UserRole
from app.security import hash_password

DEFAULT_PASSWORD = "correct horse battery staple"
_UNSET: Any = object()
_counter = itertools.count(1)
_passwords: dict[int, str] = {}

MakeUser = Callable[..., Awaitable[User]]
MakeGroup = Callable[..., Awaitable[Group]]
LoginAs = Callable[[User], Awaitable[httpx.AsyncClient]]


@pytest.fixture
def make_user(db_session: AsyncSession) -> MakeUser:
    async def _make(
        role: UserRole | str = "member",
        *,
        username: str | None = None,
        password: str | None = DEFAULT_PASSWORD,
        is_active: bool = True,
        email: str | None = None,
        oidc_issuer: str | None = None,
        oidc_subject: str | None = None,
        display_name: str | None = None,
    ) -> User:
        user = User(
            username=username or f"user{next(_counter)}",
            password_hash=hash_password(password) if password is not None else None,
            role=UserRole(role),
            is_active=is_active,
            email=email,
            oidc_issuer=oidc_issuer,
            oidc_subject=oidc_subject,
            display_name=display_name,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        if password is not None:
            _passwords[user.id] = password
        return user

    return _make


@pytest.fixture
def make_group(db_session: AsyncSession) -> MakeGroup:
    async def _make(
        name: str,
        *,
        permissions: Sequence[str] = (),
        members: Sequence[User] = (),
        printer_access: Mapping[int, tuple[bool, bool, bool]] | None = None,
    ) -> Group:
        group = Group(name=name, permissions=list(permissions))
        db_session.add(group)
        await db_session.flush()
        for u in members:
            db_session.add(GroupMember(group_id=group.id, user_id=u.id))
        for pid, (can_print, can_control, review) in (printer_access or {}).items():
            db_session.add(
                PrinterAccess(
                    group_id=group.id,
                    printer_id=pid,
                    can_print=can_print,
                    can_control=can_control,
                    requires_review=review,
                )
            )
        await db_session.commit()
        await db_session.refresh(group)
        return group

    return _make


@pytest.fixture
async def login_as(migrated_db: str, redis_url: str) -> AsyncGenerator[LoginAs, None]:
    clients: list[httpx.AsyncClient] = []

    async def _login(user: User) -> httpx.AsyncClient:
        ac = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        )
        clients.append(ac)
        r = await ac.post(
            "/api/auth/login",
            json={"username": user.username, "password": _passwords[user.id]},
        )
        assert r.status_code == 204, r.text
        return ac

    yield _login
    for ac in clients:
        await ac.aclose()


@pytest.fixture
async def viewer_client(make_user: MakeUser, login_as: LoginAs) -> httpx.AsyncClient:
    return await login_as(await make_user("viewer"))


@pytest.fixture
async def member(make_user: MakeUser) -> User:
    return await make_user("member")


@pytest.fixture
async def member_client(member: User, login_as: LoginAs) -> httpx.AsyncClient:
    return await login_as(member)


@pytest.fixture
async def redis_client(redis_url: str) -> AsyncGenerator[aioredis.Redis, None]:
    r = aioredis.from_url(redis_url)
    await r.flushdb()
    yield r
    await r.aclose()


@pytest.fixture
async def printer_row(db_session: AsyncSession) -> Printer:
    p = Printer(
        name="P1",
        kind=PrinterKind.BAMBU_LAN,
        host="10.0.0.1",
        serial="SER1",
        access_code_enc="x",
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p
