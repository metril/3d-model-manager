# P1 Multi-user, Groups, Permissions, OIDC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single-admin app into a multi-user app with roles, groups, per-printer access, a central permission enum enforced on every mutating route, user-owned API tokens, and OIDC login (Authentik first, generic OIDC too).

**Architecture:** One Alembic migration adds user columns, `groups`/`group_members`/`printer_access`/`oidc_providers`, `api_tokens.user_id` and `created_by_user_id` on 9 user-authored tables, with backfills to the existing admin. `services/permissions.py` owns the permission enum and resolution. `api/deps.py` loads grants once per request into `AuthContext` and exposes `require_perm`, `require_perm_on_write`, `require_printer_access` and `visible_printer_ids`, applied per router or per route. OIDC uses authlib's httpx OAuth2 client (PKCE + code exchange) and joserfc for ID-token validation. One-shot state lives in Redis, and login reuses the existing `sessions`/cookie.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, Postgres (citext), Redis, authlib, joserfc, respx (tests), pytest + testcontainers; React + TanStack Router/Query, vitest.

**Spec:** `docs/superpowers/specs/2026-10-21-bambuddy-parity-design.md` (§Context, §Phase order, §Integration rule, §P1)

## Global Constraints

- Branch `p1-multiuser-oidc`, created from `main` after P0 is merged. Independently mergeable, green gates, one Opus whole-branch review.
- One Alembic migration for the whole schema change. It revises head `f3a1c9d2e8b7`, and `tests/test_migration_drift.py` must stay green (models == migration).
- Permission enum, exactly: `admin`, `library:write`, `inventory:write`, `printers:print`, `printers:control`, `queue:review`, `settings:write`, `billing:manage`.
- Role base sets: viewer = {}; member = {`library:write`, `inventory:write`, `printers:print`}; admin = all. Effective = role set ∪ groups' `permissions`.
- `created_by_user_id`: nullable FK `users.id` `ON DELETE SET NULL`. Filled from `ctx.user` in API handlers; Celery/beat tasks write NULL. Never put it on derived/cache tables.
- OIDC: Redis key `oidc:state:{state}`, 10-minute TTL (600 s), one-shot. Provider defaults: scopes `"openid profile email groups"`, groups_claim `"groups"`. Callback URL `{TDMM_PUBLIC_URL or request base}/api/auth/oidc/{slug}/callback`.
- Secrets at rest use the existing Fernet `app.crypto.encrypt_secret`/`decrypt_secret`. `client_secret` is write-only and never returned or logged.
- Settings stay in the `app` row via `services/app_config.py`; env names are short with a `TDMM_` prefix. No new env vars are needed in P1.
- Leave the uncommitted 1-line `compose.yaml` change alone. Commit as metril, conventional-commit style, with no AI mention.
- Gates: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`; `cd web && npm run build && npx oxlint && npx vitest run`. pytest needs Docker (testcontainers, sandbox disabled). pytest output must stay warning-free (`filterwarnings` in `pyproject.toml`).

## Review Focus

- Last admin lockout: demoting, deactivating or deleting the last active admin (via `/admin/users` or OIDC `admin_group` demotion of a self-edit) must be refused with 409. Tests are in Task 8, plus the self-delete test.
- Open redirect through `return_to`: `//evil.com`, `https://evil.com` and `/\evil.com` must fall back to `/`. Test is in Task 11.
- Case-different email linking: `Alice@X.test` from the IdP must link to `alice@x.test` (citext), and only when `email_verified` is true. Test is in Task 11.
- OIDC group sync must not delete `source=local` memberships, and a mapping that points at a deleted group id must be skipped, not 500. Test is in Task 11.
- A deactivated user's existing session cookie and API tokens must stop working immediately: 401 on the next request. Tests are in Task 2 (session) and Task 7 (token).

---

### Task 1: Migration + ORM models for users, groups, access, OIDC providers, ownership

**Files:**
- Create: `backend/alembic/versions/c5e2a8d4b7f1_multiuser_groups_oidc.py`
- Modify: `backend/app/models/auth.py`, `backend/app/models/enums.py`, `backend/app/models/__init__.py`, `backend/app/models/library.py` (Model, Revision, File, Note, PrintQueueEntry, Print), `backend/app/models/collections.py` (FollowedCollection), `backend/app/models/system.py` (Import), `backend/app/models/printing.py` (PrintJob), `backend/tests/conftest.py` (`admin_user` gets `role=UserRole.ADMIN`), `backend/tests/test_migrations_r13c.py` (import helpers from the new module)
- Create: `backend/tests/migration_helpers.py` (move `_run_alembic` + `scratch_db` out of `test_migrations_r13c.py`; register in `conftest.pytest_plugins`)
- Test: `backend/tests/test_migrations_p1.py`

**Interfaces:**
- Produces: `UserRole(StrEnum)` = `admin|member|viewer`; `MembershipSource(StrEnum)` = `local|oidc` (both via `str_enum`). `User` gains `email: str|None` (`postgresql.CITEXT`, unique), `display_name: str|None`, `is_active: bool` (server default true), `role: UserRole` (server default `member`), `oidc_issuer`/`oidc_subject: str|None` (`UniqueConstraint("oidc_issuer","oidc_subject", name="uq_users_oidc_identity")`), `last_login_at: datetime|None`, and `password_hash: str|None`.
- Produces: `Group(id, name unique, description: str|None, permissions: list[str]` (`ARRAY(Text)`, server default `{}`), `created_at)`; `GroupMember(group_id FK cascade, user_id FK cascade, source: MembershipSource; PK(group_id,user_id))`; `PrinterAccess(group_id FK cascade, printer_id FK printers cascade, can_print, can_control, requires_review: bool default false; PK(group_id,printer_id))`; `OidcProvider(id, name, slug unique, discovery_url, client_id, client_secret_enc, scopes default "openid profile email groups", groups_claim default "groups", group_mapping: dict JSONB default {}, admin_group: str|None, auto_create_users: bool default false, link_by_email: bool default false, enabled: bool default true, created_at)`.
- Produces: `ApiToken.user_id: int` (NOT NULL, FK cascade, indexed). `created_by_user_id: Mapped[int|None]` on `models, revisions, files, notes, followed_collections, imports, print_queue, prints, print_jobs`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migrations_p1.py  (uses scratch_db + _run_alembic from tests/migration_helpers.py)
async def test_p1_upgrade_backfills_admin_tokens_and_owners(scratch_db):
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, "f3a1c9d2e8b7")
    # seed via asyncpg: users('admin','h'), api_tokens(token_hash='t',label='x'),
    # imports(url='u', site='thingiverse', state='pending')
    await _run_alembic(sa_url, command.upgrade, "c5e2a8d4b7f1")
    assert await conn.fetchval("select role from users where username='admin'") == "admin"
    assert await conn.fetchval("select is_active from users") is True
    assert await conn.fetchval("select user_id from api_tokens") == admin_id
    assert await conn.fetchval("select created_by_user_id from imports") == admin_id

async def test_p1_round_trip_with_passwordless_user(scratch_db):
    # upgrade head; insert users(username='oidc-only', password_hash=NULL, role='member')
    await _run_alembic(sa_url, command.downgrade, "f3a1c9d2e8b7")
    assert await conn.fetchval("select password_hash from users where username='oidc-only'") == "!"
    await _run_alembic(sa_url, command.upgrade, "c5e2a8d4b7f1")
    assert await conn.fetchval("select count(*) from groups") == 0

async def test_p1_upgrade_on_empty_db_succeeds(scratch_db):
    await _run_alembic(sa_url, command.upgrade, "head")
    assert await conn.fetchval("select count(*) from users") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_migrations_p1.py -q`
Expected: FAIL. Alembic reports `Can't locate revision identified by 'c5e2a8d4b7f1'`.

- [ ] **Step 3: Implement migration `c5e2a8d4b7f1` (down_revision `f3a1c9d2e8b7`) and the ORM changes**

Upgrade: `CREATE EXTENSION IF NOT EXISTS citext`. Then add columns, create tables, make `password_hash` nullable, and run the data step: `UPDATE users SET role='admin'`. Using `admin_id = (SELECT min(id) FROM users)`, set `api_tokens.user_id = admin_id`, deleting tokens if there are no users; then set NOT NULL; then set `created_by_user_id = admin_id` on the 9 tables. Downgrade: `UPDATE users SET password_hash='!' WHERE password_hash IS NULL`, then drop everything in reverse (keep the citext extension). The migration inlines its own values and does not import app code.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_migrations_p1.py tests/test_migrations_r13c.py tests/test_migration_drift.py tests/test_auth.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/c5e2a8d4b7f1_multiuser_groups_oidc.py backend/app/models backend/tests/conftest.py backend/tests/migration_helpers.py backend/tests/test_migrations_r13c.py backend/tests/test_migrations_p1.py
git commit -m "feat(db): add users roles, groups, printer access, oidc providers and created_by columns"
```

---

### Task 2: Bootstrap upsert, password-less login refusal, inactive users rejected

**Files:**
- Modify: `backend/app/services/bootstrap.py`, `backend/app/api/auth.py`, `backend/app/api/deps.py`
- Test: `backend/tests/test_bootstrap.py`, `backend/tests/test_auth.py`

**Interfaces:**
- Consumes: `User.role`, `User.is_active`, `User.password_hash: str|None`, `User.last_login_at` (Task 1)
- Produces: `async def start_session(db: AsyncSession, response: Response, user: User) -> SessionModel` in `app/api/auth.py`. It creates the session row, sets `last_login_at`, sets the cookie and commits; Task 11 reuses it.

- [ ] **Step 1: Write the failing tests**

```python
async def test_ensure_admin_user_creates_admin_role(db_session):
    await ensure_admin_user(db_session)
    assert (await db_session.scalar(select(User))).role == UserRole.ADMIN

async def test_ensure_admin_user_repromotes_existing_admin_username(db_session):
    db_session.add(User(username="admin", password_hash="x", role=UserRole.VIEWER, is_active=False))
    await db_session.commit()
    await ensure_admin_user(db_session)
    u = await db_session.scalar(select(User).where(User.username == "admin"))
    assert (u.role, u.is_active) == (UserRole.ADMIN, True)
    assert await db_session.scalar(select(func.count()).select_from(User)) == 1

async def test_login_refused_when_password_hash_null(client, db_session):
    db_session.add(User(username="sso", password_hash=None, role=UserRole.MEMBER)); await db_session.commit()
    r = await client.post("/api/auth/login", json={"username": "sso", "password": "anything"})
    assert r.status_code == 401 and r.json()["detail"] == "Invalid username or password"

async def test_login_refused_for_inactive_user(client, db_session): ...  # 401, same detail
async def test_login_sets_last_login_at(authenticated_client, admin_user, db_session):
    await db_session.refresh(admin_user); assert admin_user.last_login_at is not None

async def test_existing_session_401_after_deactivation(authenticated_client, admin_user, db_session):
    admin_user.is_active = False; await db_session.commit()
    assert (await authenticated_client.get("/api/auth/me")).status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_bootstrap.py tests/test_auth.py -q`
Expected: FAIL. The new tests fail: role is `member`, verify on `None` raises/returns wrong, and `/auth/me` returns 200 for the inactive user.

- [ ] **Step 3: Implement**

- `ensure_admin_user(session)`: when zero users exist, create as today with `role=ADMIN`. Otherwise, if a user named `settings.admin_username` exists, set `role=ADMIN, is_active=True` (this is the lockout recovery path) and leave the password alone.
- `login`: run `verify_password(payload.password, DUMMY_HASH)` and raise `_invalid_credentials()` when `user is None or user.password_hash is None or not user.is_active`, then call `start_session`.
- `require_session`: 401 when `not user.is_active`.
- `change_password`: 403 `"Current password is incorrect"` when `password_hash is None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_bootstrap.py tests/test_auth.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/bootstrap.py backend/app/api/auth.py backend/app/api/deps.py backend/tests/test_bootstrap.py backend/tests/test_auth.py
git commit -m "feat(auth): upsert bootstrap admin role, refuse passwordless and inactive logins"
```

---

### Task 3: Permission enum + resolver service

**Files:**
- Create: `backend/app/services/permissions.py`
- Test: `backend/tests/test_permissions.py`

**Interfaces:**
- Consumes: `UserRole`, `User`, `Group`, `GroupMember`, `PrinterAccess` (Task 1)
- Produces:
  - `PERMS: tuple[str, ...]` (the 8 names, in spec order), `Perm = Literal[...]`
  - `def role_base(role: UserRole) -> frozenset[str]`
  - `def effective_perms(user: User, groups: Sequence[Group]) -> frozenset[str]`. If `"admin"` is in the union, it returns `frozenset(PERMS)`.
  - `def validate_perms(perms: Iterable[str]) -> list[str]`. It raises `ValueError(f"unknown permission: {p}")` and returns deduplicated, sorted output.
  - `@dataclass(frozen=True) class PrinterAcl: can_print: bool; can_control: bool; requires_review: bool`, with `FULL_ACL = PrinterAcl(True, True, False)`
  - `def merge_printer_acl(rows: Iterable[PrinterAccess]) -> dict[int, PrinterAcl]` (OR of print/control, OR of requires_review)
  - `def printer_perms(perms: frozenset[str], acl: Mapping[int, PrinterAcl], printer_id: int) -> PrinterAcl | None`. It returns `FULL_ACL` when `"admin" in perms`, otherwise `acl.get(printer_id)`.
  - `async def load_grants(db: AsyncSession, user: User) -> tuple[list[Group], frozenset[str], dict[int, PrinterAcl]]` (two queries: groups via `group_members`, then `printer_access` for those groups)

- [ ] **Step 1: Write the failing test**

```python
def test_role_bases():
    assert role_base(UserRole.VIEWER) == frozenset()
    assert role_base(UserRole.MEMBER) == {"library:write", "inventory:write", "printers:print"}
    assert role_base(UserRole.ADMIN) == frozenset(PERMS)

def test_effective_perms_unions_groups():
    u = User(username="v", role=UserRole.VIEWER)
    assert effective_perms(u, [Group(name="g", permissions=["queue:review"])]) == {"queue:review"}

def test_group_granting_admin_yields_all():
    u = User(username="m", role=UserRole.MEMBER)
    assert effective_perms(u, [Group(name="a", permissions=["admin"])]) == frozenset(PERMS)

def test_validate_perms_rejects_unknown():
    with pytest.raises(ValueError, match="unknown permission: printers:fly"):
        validate_perms(["printers:fly"])

def test_merge_printer_acl_ors_across_groups():
    rows = [PrinterAccess(group_id=1, printer_id=7, can_print=True, can_control=False, requires_review=False),
            PrinterAccess(group_id=2, printer_id=7, can_print=False, can_control=True, requires_review=True)]
    assert merge_printer_acl(rows)[7] == PrinterAcl(True, True, True)

def test_printer_perms_admin_full_member_none():
    assert printer_perms(frozenset(PERMS), {}, 3) == FULL_ACL
    assert printer_perms(frozenset({"printers:print"}), {}, 3) is None

async def test_load_grants_reads_memberships(db_session): ...  # viewer in group g(perms=["library:write"], printer 1 can_print) -> perms {"library:write"}, acl {1: PrinterAcl(True, False, False)}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_permissions.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.permissions'`

- [ ] **Step 3: Implement the functions above in `backend/app/services/permissions.py`**

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_permissions.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/permissions.py backend/tests/test_permissions.py
git commit -m "feat(auth): add central permission enum and grant resolver"
```

---

### Task 4: AuthContext with grants, require_perm, printer ACL deps, visible_printer_ids, user fixtures

**Files:**
- Modify: `backend/app/api/deps.py`, `backend/tests/conftest.py` (add `"tests.auth_fixtures"` to `pytest_plugins`)
- Create: `backend/tests/auth_fixtures.py`
- Test: `backend/tests/test_auth_context.py`

**Interfaces:**
- Consumes: `load_grants`, `printer_perms`, `PrinterAcl`, `PERMS`, `Perm` (Task 3)
- Produces (in `app/api/deps.py`):
  - `@dataclass(slots=True) class AuthContext: user: User; session: Session | None; perms: frozenset[str]; printer_acl: dict[int, PrinterAcl]; groups: list[Group]`, with `is_admin` (property: `"admin" in perms`), `can(perm: str) -> bool`, and `require_printer(printer_id: int, kind: Literal["print","control"]) -> None`. `require_printer` raises 403 `"printer access denied"` unless `f"printers:{kind}" in perms` AND the ACL flag (`can_print`/`can_control`) is true; admins always pass.
  - `require_session` returns the populated `AuthContext` (one `load_grants` call per request; FastAPI's per-request dep cache keeps it single).
  - `def require_perm(*perms: Perm) -> Callable[..., Awaitable[AuthContext]]`: all listed perms are required, else 403 `"missing permission: <p>"`. It raises `ValueError` at definition time for a name not in `PERMS`.
  - `def require_perm_on_write(*perms: Perm)`: the same check, skipped for `GET`/`HEAD`/`OPTIONS` (reads `Request.method`).
  - `def require_printer_access(kind: Literal["print","control"])`: a dependency taking the `printer_id: int` path param and calling `ctx.require_printer`.
  - `def visible_printer_ids(ctx: AuthContext) -> set[int] | None`. `None` means all (admin); otherwise it is the set of printer ids that have any `printer_access` row in the user's groups.
- Produces (test fixtures in `tests/auth_fixtures.py`):
  - `make_user(role="member", *, username=None, password=..., is_active=True) -> User`
  - `make_group(name, *, permissions=(), members=(), printer_access={printer_id: (can_print, can_control, requires_review)}) -> Group`
  - `login_as(user) -> httpx.AsyncClient`: a fresh ASGI client logged in via `/api/auth/login`, closed at teardown.

- [ ] **Step 1: Write the failing test**

```python
# a throwaway router mounted on create_app() under /api/_t with dependencies=[Depends(require_session)]
#   GET  /_t/lib      -> Depends(require_perm("library:write"))
#   POST /_t/settings -> router-level require_perm_on_write("settings:write"); GET /_t/settings open
#   POST /_t/printers/{printer_id}/go -> Depends(require_printer_access("control"))
async def test_viewer_403_member_200_on_library_write(make_user, login_as): ...
    # viewer: 403 detail "missing permission: library:write"; member: 200
async def test_perm_on_write_allows_get(...):  # viewer GET 200, POST 403
async def test_printer_access_requires_perm_and_acl(make_user, make_group, login_as, printer_row):
    # member + group(printers:control perm, acl {pid: (True, True, False)}) -> 200
    # member + acl can_control but no printers:control perm -> 403 "printer access denied"
    # admin with no groups -> 200
def test_require_perm_rejects_unknown_name():
    with pytest.raises(ValueError): require_perm("library:wrote")
async def test_visible_printer_ids(...):  # admin -> None; member in group with acl for 1 and 2 -> {1, 2}; no groups -> set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_auth_context.py -q`
Expected: FAIL with `ImportError: cannot import name 'require_perm' from 'app.api.deps'`

- [ ] **Step 3: Implement the interfaces above in `backend/app/api/deps.py` and `backend/tests/auth_fixtures.py`**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_auth_context.py tests/test_auth.py tests/test_auth_sweep.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/deps.py backend/tests/auth_fixtures.py backend/tests/conftest.py backend/tests/test_auth_context.py
git commit -m "feat(auth): load grants into AuthContext and add require_perm and printer ACL deps"
```

---

### Task 5: Gate every router + viewer-403 sweep (CI guard for P2–P8)

**Files:**
- Modify: `backend/app/api/__init__.py`, `settings.py` (router-level `require_perm_on_write("settings:write")`; move the three api-token routes to a new `api_tokens_router = APIRouter(prefix="/settings", tags=["settings"])` with no perm, included in `protected_router`), `models.py`, `revisions.py`, `files.py`, `tags.py`, `notes.py`, `uploads.py`, `imports.py`, `collections.py`, `categories.py`, `jobs.py`, `reports.py`, `prints.py` (per-route `Depends(require_perm("library:write"))` on every POST/PUT/PATCH/DELETE except `POST /files/{file_id}/slicer-link`), `materials.py` (per-route `inventory:write`), `queue.py` (per-route `printers:print`), `scan.py` (per-route `settings:write`), `printers.py` (create/detect-serial/patch/delete/test → `require_perm("settings:write")`; `/{printer_id}/print` → `require_printer_access("print")`; pause/resume/stop → `require_printer_access("control")`; `GET ""` filtered by `visible_printer_ids`; `GET /{printer_id}` and `/status` → 404 when not visible), `print_jobs.py` (list filtered by `visible_printer_ids`)
- Test: `backend/tests/test_perm_sweep.py`, `backend/tests/test_perm_matrix.py`

**Interfaces:**
- Consumes: `require_perm`, `require_perm_on_write`, `require_printer_access`, `visible_printer_ids` (Task 4)
- Produces: `VIEWER_ALLOWLIST` in `tests/test_perm_sweep.py`, the list P2–P8 extend:
  `{("POST","/api/auth/logout"), ("POST","/api/auth/password"), ("POST","/api/settings/api-tokens"), ("DELETE","/api/settings/api-tokens/{token_id}"), ("POST","/api/files/{file_id}/slicer-link")}`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_perm_sweep.py -- reuses _all_routes/_concrete_path/PUBLIC_ROUTES from tests/test_auth_sweep.py
_TOKEN_PLANE = ("/api/ext/", "/api/slicer/")
_MUTATING = [(m, p) for m, p in _all_routes()
             if m in {"POST", "PUT", "PATCH", "DELETE"} and (m.lower(), p) not in PUBLIC_ROUTES
             and not p.startswith(_TOKEN_PLANE) and (m, p) not in VIEWER_ALLOWLIST]

@pytest.mark.usefixtures("printer_enabled")
@pytest.mark.parametrize("method,path", _MUTATING, ids=[f"{m} {p}" for m, p in _MUTATING])
async def test_viewer_gets_403_on_mutating_route(viewer_client, method, path):
    r = await viewer_client.request(method, _concrete_path(path), json={} if method != "DELETE" else None)
    assert r.status_code == 403, f"{method} {path} -> {r.status_code}; add require_perm or allowlist it"

def test_mutating_sweep_not_empty(): assert len(_MUTATING) >= 60
def test_allowlist_entries_exist(): assert VIEWER_ALLOWLIST <= set(_all_routes())

# tests/test_perm_matrix.py (role x group x route, real handlers)
@pytest.mark.parametrize("role,group_perms,method,path,expected", [
    ("viewer", [], "PUT", "/api/settings/app", 403),
    ("viewer", ["settings:write"], "PUT", "/api/settings/app", 422),  # passes gate, body invalid
    ("member", [], "POST", "/api/materials", 422),
    ("viewer", [], "POST", "/api/queue", 403),
    ("member", [], "GET", "/api/settings/app", 200),
    ("member", [], "PUT", "/api/settings/app", 403),
])
async def test_perm_matrix(...): ...
async def test_member_without_acl_cannot_print(...)  # POST /api/printers/{id}/print -> 403
async def test_member_lists_only_visible_printers(...)  # 2 printers, acl on 1 -> GET /api/printers returns [that id]; GET other -> 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_perm_sweep.py tests/test_perm_matrix.py -q`
Expected: FAIL, many routes return 404/422/201 for the viewer instead of 403.

- [ ] **Step 3: Apply the dependencies listed under Files**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_perm_sweep.py tests/test_perm_matrix.py tests/test_auth_sweep.py tests/test_settings_api.py tests/test_printers_api.py tests/test_queue_api.py tests/test_materials_api.py -q`
Expected: PASS (existing suites use the admin fixture, so they stay green)

- [ ] **Step 5: Commit**

```bash
git add backend/app/api backend/tests/test_perm_sweep.py backend/tests/test_perm_matrix.py
git commit -m "feat(auth): enforce permissions on every mutating route with viewer 403 sweep"
```

---

### Task 6: Attribution on create + creator-or-admin deletes

**Files:**
- Modify: `backend/app/api/models.py`, `uploads.py`, `revisions.py`, `notes.py`, `imports.py`, `collections.py`, `queue.py`, `prints.py`, `printers.py` (print → `PrintJob`), plus the sync service functions they call (add a `created_by_user_id: int | None = None` kwarg); `backend/app/api/files.py`; `backend/app/schemas/library.py` (`ModelDetail.created_by_user_id: int | None`, `ModelDetail.created_by_username: str | None`)
- Test: `backend/tests/test_ownership.py`

**Interfaces:**
- Consumes: `AuthContext.user`, `AuthContext.is_admin` (Task 4)
- Produces: `def require_owner_or_admin(ctx: AuthContext, created_by_user_id: int | None) -> None` in `app/api/deps.py`, raising 403 `"only the creator or an admin can delete this"`. It is applied to `DELETE /models/{slug}`, `POST /models/bulk-delete` (whole request 403 if any slug isn't deletable), `DELETE /files/{file_id}` and `DELETE /notes/{note_id}`. Rows with NULL creator can only be deleted by admins.

- [ ] **Step 1: Write the failing test**

```python
async def test_create_model_records_creator(member_client, member): r = await member_client.post("/api/models", json={...})
    assert r.json()["created_by_user_id"] == member.id and r.json()["created_by_username"] == member.username
async def test_note_and_queue_and_import_record_creator(...)  # created_by_user_id == member.id on each row
async def test_member_cannot_delete_others_model(...)  # 403 "only the creator or an admin can delete this"
async def test_member_can_delete_own_model(...)  # 204
async def test_admin_deletes_any_model(...)  # 204
async def test_bulk_delete_with_one_foreign_slug_is_403_and_deletes_nothing(...)
async def test_null_creator_model_only_admin_deletes(...)  # member 403, admin 204
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_ownership.py -q`
Expected: FAIL with `KeyError: 'created_by_user_id'` and 204 instead of 403

- [ ] **Step 3: Implement**

Set `created_by_user_id=ctx.user.id` wherever an API handler (or a sync service it calls) inserts one of the 9 tables. Celery task code paths pass nothing, so they write NULL. Add `require_owner_or_admin` and the `ModelDetail` fields (username via a join or relationship).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_ownership.py tests/test_models_api.py tests/test_tags_notes_api.py tests/test_uploads_api.py tests/test_queue_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api backend/app/services backend/app/schemas/library.py backend/tests/test_ownership.py
git commit -m "feat(library): record created_by on user-created rows and restrict deletes to creator or admin"
```

---

### Task 7: `/auth/me` shape, user-scoped API tokens, token plane acts as owner with capped perms

**Files:**
- Modify: `backend/app/api/auth.py`, `backend/app/services/api_tokens.py`, `backend/app/api/settings.py` (`api_tokens_router`), `backend/app/schemas/settings.py` (`ApiTokenOut` + `user_id`, `username`), `backend/app/api/deps.py`, `backend/app/api/ext.py`, `backend/app/api/slicer.py`, `backend/tests/test_ext_api.py` + `test_api_tokens*.py` + `test_slicer_intake_api.py` (fixtures pass `user_id=admin_user.id`)
- Test: `backend/tests/test_auth_me.py`, `backend/tests/test_token_scoping.py`

**Interfaces:**
- Consumes: `AuthContext`, `load_grants`, `require_perm` (Tasks 3–4)
- Produces:
  - `GET /auth/me` → `MeOut{id: int, username: str, display_name: str|None, email: str|None, role: str, permissions: list[str] (sorted), groups: list[{id: int, name: str}], has_password: bool, printer_access: dict[str, {print: bool, control: bool}] | None}` (`None` for admins). The extra `printer_access` field feeds web `canPrinter`.
  - `api_tokens.mint(db, *, label: str, user_id: int) -> tuple[str, ApiToken]`; `list_tokens(db, *, user_id: int | None) -> list[ApiToken]` (None = all); `revoke(db, token_id: int, *, user_id: int | None) -> bool`
  - `TOKEN_PERM_CAP = frozenset({"library:write", "printers:print"})`
  - `async def require_token_context(token: ApiToken = Depends(require_api_token), db=...) -> AuthContext`: owner must be active (else 401), `session=None`, `perms = owner_perms & TOKEN_PERM_CAP`.
  - `def require_token_perm(*perms: Perm)`: a dependency on `require_token_context`, 403 when a perm is missing. `/ext` POST routes and `/slicer/intake` get `require_token_perm("library:write")`, and imports created there record `created_by_user_id = token owner`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_me_shape_for_admin(authenticated_client, admin_user):
    body = (await authenticated_client.get("/api/auth/me")).json()
    assert body == {"id": admin_user.id, "username": "admin", "display_name": None, "email": None,
                    "role": "admin", "permissions": sorted(PERMS), "groups": [], "has_password": True,
                    "printer_access": None}
async def test_me_member_with_group(...)  # permissions == ["inventory:write","library:write","printers:print","queue:review"], groups == [{"id": g.id, "name": "reviewers"}], printer_access == {"1": {"print": True, "control": False}}

async def test_member_lists_only_own_tokens(...)  # member sees 1 of 2; admin sees 2
async def test_member_cannot_revoke_others_token(...)  # DELETE -> 404
async def test_viewer_token_403_on_ext_import(...)  # POST /api/ext/imports with viewer-owned token -> 403
async def test_admin_token_capped_cannot_reach_session_api(...)  # bearer on PUT /api/settings/app -> 401 (unchanged plane isolation)
async def test_ext_import_records_token_owner(...)  # imports.created_by_user_id == member.id
async def test_token_of_deactivated_user_401(...)  # GET /api/ext/ping -> 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_auth_me.py tests/test_token_scoping.py -q`
Expected: FAIL. `/auth/me` returns only `{"username": ...}`, and `mint()` got an unexpected keyword `user_id`.

- [ ] **Step 3: Implement the interfaces above**

The token list/revoke handlers pass `user_id=None if ctx.is_admin else ctx.user.id`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_auth_me.py tests/test_token_scoping.py tests/test_ext_api.py tests/test_api_tokens.py tests/test_api_tokens_settings_api.py tests/test_slicer_intake_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat(auth): expand /auth/me, scope api tokens to users and cap token permissions"
```

---

### Task 8: Admin users API

**Files:**
- Create: `backend/app/api/admin.py` (`router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_perm("admin"))])`, included in `protected_router`), `backend/app/schemas/admin.py`
- Modify: `backend/app/api/__init__.py`, `backend/tests/test_auth_sweep.py` (`_PATH_PARAM_VALUES` += `"user_id": "1"`)
- Test: `backend/tests/test_admin_users_api.py`

**Interfaces:**
- Produces:
  - `UserAdminOut{id, username, display_name, email, role, is_active, has_password, oidc_linked: bool, last_login_at, groups: list[{id, name}]}`
  - `GET /admin/users`
  - `POST /admin/users` `UserCreateIn{username, password: str|None (min 8), display_name, email, role="member"}` → 201
  - `PATCH /admin/users/{user_id}` `UserPatchIn{display_name?, email?, role?, is_active?}`
  - `POST /admin/users/{user_id}/password` `{new_password (min 8)}` → 204, deletes that user's sessions
  - `DELETE /admin/users/{user_id}` → 204
- Rules: duplicate username/email → 409 `"username or email already in use"`. Deactivation deletes all of the user's sessions. Any change that leaves zero active admins → 409 `"cannot remove the last admin"`. Deleting yourself → 409 `"cannot delete yourself"`.

- [ ] **Step 1: Write the failing test**

```python
async def test_create_list_user(authenticated_client): ...  # 201; list contains {"username":"bob","role":"member","has_password":True,"is_active":True}
async def test_duplicate_email_case_insensitive_409(...)  # "Bob@X.test" after "bob@x.test" -> 409
async def test_deactivate_kills_sessions(...)  # bob logged in; PATCH is_active False; bob's client GET /api/auth/me -> 401
async def test_reset_password(...)  # bob logs in with new password -> 204; old session -> 401
async def test_cannot_demote_last_admin(authenticated_client, admin_user):
    r = await authenticated_client.patch(f"/api/admin/users/{admin_user.id}", json={"role": "member"})
    assert r.status_code == 409 and r.json()["detail"] == "cannot remove the last admin"
async def test_cannot_deactivate_last_admin(...)  # 409 same detail
async def test_cannot_delete_self(...)  # 409 "cannot delete yourself"
async def test_member_403_on_admin_users(member_client): assert (await member_client.get("/api/admin/users")).status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_admin_users_api.py -q`
Expected: FAIL with 404 on `/api/admin/users`

- [ ] **Step 3: Implement the routes in `backend/app/api/admin.py`**

Hash passwords with `app.security.hash_password`. The last-admin check counts `role='admin' AND is_active` users other than the target.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_admin_users_api.py tests/test_auth_sweep.py tests/test_perm_sweep.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/admin.py backend/app/api/__init__.py backend/app/schemas/admin.py backend/tests/test_admin_users_api.py backend/tests/test_auth_sweep.py
git commit -m "feat(admin): add user management API with last-admin guard"
```

---

### Task 9: Admin groups, members, printer-access API

**Files:**
- Modify: `backend/app/api/admin.py`, `backend/app/schemas/admin.py`, `backend/tests/test_auth_sweep.py` (`_PATH_PARAM_VALUES` += `"group_id": "1"`)
- Test: `backend/tests/test_admin_groups_api.py`

**Interfaces:**
- Consumes: `validate_perms` (Task 3)
- Produces:
  - `GroupOut{id, name, description, permissions: list[str], members: list[{user_id, username, source}], printer_access: list[{printer_id, can_print, can_control, requires_review}]}`
  - `GET/POST /admin/groups`, `PATCH/DELETE /admin/groups/{group_id}`
  - `PUT /admin/groups/{group_id}/members` `{user_ids: list[int]}`: replaces `source=local` rows only and returns `GroupOut`
  - `PUT /admin/groups/{group_id}/printer-access` `list[{printer_id, can_print, can_control, requires_review}]`: replaces all rows and returns `GroupOut`
- Rules: unknown permission → 422 (message from `validate_perms`); duplicate name → 409; unknown user/printer id → 422.

- [ ] **Step 1: Write the failing test**

```python
async def test_create_group_with_perms(...)  # {"name":"makers","permissions":["queue:review","library:write"]} -> permissions == ["library:write","queue:review"]
async def test_unknown_perm_422(...)  # "printers:fly" -> 422
async def test_members_put_keeps_oidc_rows(...)  # seed oidc membership for u2; PUT {"user_ids":[u1]} -> members == [u1 local, u2 oidc]
async def test_printer_access_put_replaces(...)  # PUT [{printer_id:p1, can_print:True,...}] then [] -> printer_access == []
async def test_group_grant_takes_effect_next_request(...)  # viewer; add to group(library:write); viewer POST /api/notes -> not 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_admin_groups_api.py -q`
Expected: FAIL with 404 on `/api/admin/groups`

- [ ] **Step 3: Implement the routes in `backend/app/api/admin.py`**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_admin_groups_api.py tests/test_auth_sweep.py tests/test_perm_sweep.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/admin.py backend/app/schemas/admin.py backend/tests/test_admin_groups_api.py backend/tests/test_auth_sweep.py
git commit -m "feat(admin): add groups, membership and per-printer access API"
```

---

### Task 10: OIDC providers admin API (write-only Fernet secret)

**Files:**
- Create: `backend/app/api/admin_oidc.py` (`router = APIRouter(prefix="/admin/oidc-providers", dependencies=[Depends(require_perm("admin"))])`)
- Modify: `backend/app/api/__init__.py`, `backend/app/schemas/admin.py`, `backend/tests/test_auth_sweep.py` (`"provider_id": "1"`)
- Test: `backend/tests/test_admin_oidc_api.py`

**Interfaces:**
- Produces:
  - `OidcProviderOut{id, name, slug, discovery_url, client_id, has_client_secret: bool, scopes, groups_claim, group_mapping: dict[str, int], admin_group, auto_create_users, link_by_email, enabled}`
  - `POST ""` `OidcProviderCreateIn` (requires `client_secret`; slug `^[a-z0-9][a-z0-9-]{0,62}$`; `discovery_url` must start with `https://`) → 201
  - `GET ""`
  - `PATCH /{provider_id}` (`client_secret` omitted or null keeps the stored secret)
  - `DELETE /{provider_id}`
  - `group_mapping` values must be existing group ids, else 422. Duplicate slug → 409.
- Storage: `client_secret_enc = encrypt_secret(get_settings(), client_secret)`.

- [ ] **Step 1: Write the failing test**

```python
async def test_create_provider_never_returns_secret(authenticated_client, db_session):
    r = await authenticated_client.post("/api/admin/oidc-providers", json={"name": "Authentik", "slug": "authentik",
        "discovery_url": "https://auth.example/application/o/tdmm/.well-known/openid-configuration",
        "client_id": "cid", "client_secret": "s3cret"})
    assert r.status_code == 201 and "client_secret" not in r.json() and r.json()["has_client_secret"] is True
    assert r.json()["scopes"] == "openid profile email groups" and r.json()["groups_claim"] == "groups"
    row = await db_session.get(OidcProvider, r.json()["id"])
    assert row.client_secret_enc != "s3cret" and decrypt_secret(get_settings(), row.client_secret_enc) == "s3cret"
async def test_patch_without_secret_keeps_it(...)
async def test_http_discovery_url_422(...); async def test_bad_slug_422(...)  # "Auth Entik"
async def test_group_mapping_unknown_group_422(...)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_admin_oidc_api.py -q`
Expected: FAIL with 404

- [ ] **Step 3: Implement the routes in `backend/app/api/admin_oidc.py`**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_admin_oidc_api.py tests/test_auth_sweep.py tests/test_perm_sweep.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/admin_oidc.py backend/app/api/__init__.py backend/app/schemas/admin.py backend/tests/test_admin_oidc_api.py backend/tests/test_auth_sweep.py
git commit -m "feat(admin): add OIDC provider management with write-only encrypted secret"
```

---

### Task 11: OIDC login flow (authlib + Redis state + resolution + group sync) and docs

**Files:**
- Modify: `backend/pyproject.toml` + `backend/uv.lock` (`uv add authlib joserfc`, `uv add --dev respx`)
- Create: `backend/app/services/oidc.py`, `backend/app/api/oidc.py` (`public_router = APIRouter(prefix="/auth/oidc", tags=["auth"])`, mounted on `api_router` beside `auth.public_router`), `backend/tests/oidc_fixtures.py` (registered in `pytest_plugins`)
- Modify: `backend/app/api/__init__.py`, `backend/tests/test_auth_sweep.py` (`PUBLIC_ROUTES` += `("get","/api/auth/oidc/providers")`, `("get","/api/auth/oidc/{slug}/start")`, `("get","/api/auth/oidc/{slug}/callback")`), `README.md` (section "Users, groups and single sign-on" with an Authentik setup snippet), `.env.example` (note that `TDMM_PUBLIC_URL` sets the OIDC callback origin behind a proxy)
- Test: `backend/tests/test_oidc_flow.py`, `backend/tests/test_oidc_claims.py`

**Interfaces:**
- Consumes: `OidcProvider`, `GroupMember`, `MembershipSource` (Task 1); `start_session` (Task 2); `decrypt_secret`
- Produces (`app/services/oidc.py`):
  - `class OidcError(Exception): code: Literal["not_provisioned","inactive","invalid_state","oidc_failed"]`
  - `STATE_TTL_S = 600`, `STATE_KEY = "oidc:state:{state}"`
  - `def safe_return_to(value: str | None) -> str`: returns `value` only if it starts with `/` and not with `//` or `/\`, else `"/"`.
  - `def extract_claim(claims: Mapping, path: str) -> list[str]`: dotted path; a str becomes `[str]`, a list is kept, missing becomes `[]`.
  - `async def start_login(redis, provider: OidcProvider, *, redirect_uri: str, return_to: str | None) -> str`: discovery fetch via httpx, `AsyncOAuth2Client(..., code_challenge_method="S256").create_authorization_url(..., state, nonce, code_verifier)`; stores JSON `{slug, nonce, code_verifier, return_to}` with `ex=600`.
  - `async def complete_login(db, redis, provider: OidcProvider, *, code: str, state: str, redirect_uri: str) -> tuple[User, str]` (user, return_to): `GETDEL` the state (missing or slug mismatch → `invalid_state`), `fetch_token(code, code_verifier)`, verify the ID token with `joserfc.jwt.decode` against the JWKS and check `iss` == discovery issuer, `aud` contains `client_id`, `nonce` matches and `exp` is in the future (any failure → `oidc_failed`). Userinfo claims are merged under ID-token claims.
  - `async def resolve_user(db, provider, claims) -> User`. Order: `(iss, sub)` → else `link_by_email and email_verified is True` matching citext `email` (sets `oidc_issuer/subject`) → else `auto_create_users` JIT (role `member`; username = `preferred_username` → email local part → `sub`, `-2`, `-3`… on collision; email stored only if verified and unused; `password_hash=None`) → else `OidcError("not_provisioned")`. Inactive → `OidcError("inactive")`.
  - `async def sync_groups(db, provider, user, claim_values: list[str]) -> None`: replaces `source=oidc` rows with `{group_mapping[v] for v in claim_values if v in group_mapping and the group exists}` and leaves local rows untouched. If `admin_group` is set: in claims → role admin; not in claims and role admin → member.
- Produces (`app/api/oidc.py`):
  - `GET /auth/oidc/providers` → `[{slug, name}]` for enabled providers
  - `GET /{slug}/start?return_to=` → 302 to the IdP
  - `GET /{slug}/callback?code=&state=&error=` → 302 to `return_to` with the session cookie set via `start_session`, or 302 to `/login?error=<code>`. An unknown or disabled slug on start/callback, or an IdP `error` param, → `/login?error=oidc_failed`.
  - `redirect_uri = f"{(settings.public_url or str(request.base_url)).rstrip('/')}/api/auth/oidc/{slug}/callback"`
- Fake IdP fixture `fake_idp` (`tests/oidc_fixtures.py`): an RSA key from `cryptography`; respx routes for discovery `https://idp.test/application/o/tdmm/.well-known/openid-configuration` (issuer `https://idp.test/application/o/tdmm/`), JWKS, token (signs `fake_idp.claims` + `nonce=fake_idp.nonce_override or <nonce from the start redirect>`) and userinfo. `fake_idp.login(client, provider_slug, claims) -> httpx.Response` drives start → callback and returns the callback response. It also provides a `provider` factory fixture `make_provider(**overrides) -> OidcProvider`.

- [ ] **Step 1: Write the failing tests**

```python
# test_oidc_claims.py
def test_extract_claim_dotted(): assert extract_claim({"realm_access": {"roles": ["a", "b"]}}, "realm_access.roles") == ["a", "b"]
def test_extract_claim_string_and_missing(): assert extract_claim({"groups": "x"}, "groups") == ["x"]; assert extract_claim({}, "groups") == []
@pytest.mark.parametrize("v", ["//evil.com", "https://evil.com", "/\\evil.com", None, ""])
def test_safe_return_to_rejects(v): assert safe_return_to(v) == "/"
def test_safe_return_to_keeps_path(): assert safe_return_to("/models/x?y=1") == "/models/x?y=1"

# test_oidc_flow.py
async def test_happy_path_links_existing_subject(client, fake_idp, make_provider, make_user): ...
    # user with oidc_issuer/subject preset -> callback 302 Location "/", cookie set; /api/auth/me username matches
async def test_start_stores_one_shot_state_with_ttl(client, redis_client, ...):
    # 302 Location contains code_challenge_method=S256; redis ttl("oidc:state:<state>") in (590, 600]
async def test_bad_nonce_rejected(...)  # fake_idp.nonce_override="wrong" -> Location "/login?error=oidc_failed"
async def test_reused_state_rejected(...)  # second callback with same state -> "/login?error=invalid_state"
async def test_expired_state_rejected(...)  # redis delete key before callback -> "/login?error=invalid_state"
async def test_unverified_email_not_linked(...)  # link_by_email=True, auto_create False, existing alice@x.test, claims email_verified False -> not_provisioned; alice.oidc_subject is None
async def test_verified_email_links_case_insensitive(...)  # claims email "Alice@X.test", verified -> logged in as alice; alice.oidc_subject == "sub-1"
async def test_jit_off_not_provisioned(...)  # -> "/login?error=not_provisioned"
async def test_jit_on_creates_member(...)  # role "member", has_password False, username "newbie" from preferred_username
async def test_inactive_user_rejected(...)  # -> "/login?error=inactive"
async def test_group_mapping_and_admin_promotion_then_demotion(...):
    # provider group_mapping {"makers": g.id}, admin_group "tdmm-admins"; claims groups ["makers","tdmm-admins"]
    # -> me.role == "admin", me.groups == [{"id": g.id, "name": "makers"}]
    # second login groups [] -> me.role == "member", me.groups == [] ; a pre-existing local membership survives
async def test_mapping_to_deleted_group_skipped(...)  # no 500; login succeeds
async def test_return_to_honoured(...)  # start?return_to=/printer -> callback Location "/printer"
async def test_providers_list_public_enabled_only(client, ...)  # [{"slug":"authentik","name":"Authentik"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_oidc_claims.py tests/test_oidc_flow.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.oidc'`

- [ ] **Step 3: Implement the service, router, fixtures and docs as specified in Interfaces**

The Redis client is `redis.asyncio.Redis.from_url(settings.redis_url)`, the same pattern as `app/api/printers.py`. In the README Authentik snippet, use: redirect URI `https://<host>/api/auth/oidc/authentik/callback`, a confidential client, scopes `openid profile email groups`, discovery `https://auth.example/application/o/<slug>/.well-known/openid-configuration`, and require `email_verified`. Add a Keycloak note: `groups_claim = realm_access.roles`. If authlib emits a DeprecationWarning on import, add a targeted `filterwarnings` entry rather than tolerating noisy output.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_oidc_claims.py tests/test_oidc_flow.py tests/test_auth_sweep.py tests/test_perm_sweep.py -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS, no warnings in the pytest summary

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/services/oidc.py backend/app/api/oidc.py backend/app/api/__init__.py backend/tests/oidc_fixtures.py backend/tests/conftest.py backend/tests/test_oidc_flow.py backend/tests/test_oidc_claims.py backend/tests/test_auth_sweep.py README.md .env.example
git commit -m "feat(auth): add OIDC login with one-shot state, account resolution and group sync"
```

---

### Task 12: Web auth: `can()`/`canPrinter()`, nav perm filtering, login providers + error banner, write-button gating

**Files:**
- Create: `web/src/lib/permissions.ts`, `web/src/lib/permissions.test.ts`
- Modify: `web/src/api/auth.ts` (`Me` matches Task 7's `MeOut`; `useAuth()` adds `can`/`canPrinter`; `useOidcProviders()` querying `/auth/oidc/providers`), `web/src/components/shell/navItems.ts` (+ `perm?: Perm`; Duplicates `perm: "library:write"`; export `filterNavItems`), `navItems.test.ts`, `AppSidebar.tsx`, `CommandPalette.tsx` (use `filterNavItems`), `web/src/routes.tsx` (login `validateSearch` → `{ error?: string }`), `web/src/pages/LoginPage.tsx` + `LoginPage.test.tsx`, `web/src/pages/LibraryPage.tsx` (upload/import buttons need `can("library:write")`), `web/src/components/model-detail/ModelHeader.tsx` (delete item only when `me.role==="admin" || model.created_by_user_id===me.id`), `web/src/api/types.ts` (`ModelDetail.created_by_user_id`, `created_by_username`)

**Interfaces:**
- Consumes: `GET /auth/me` shape (Task 7), `GET /auth/oidc/providers` (Task 11)
- Produces:
  - `type Perm = "admin" | "library:write" | "inventory:write" | "printers:print" | "printers:control" | "queue:review" | "settings:write" | "billing:manage"`
  - `function can(me: Me | undefined, perm: Perm): boolean`
  - `function canPrinter(me: Me | undefined, printerId: number, kind: "print" | "control"): boolean`: true when the user has the `printers:{kind}` perm and either `printer_access === null` or `printer_access[String(id)][kind]`
  - `useAuth(): UseQueryResult<Me> & { can(perm: Perm): boolean; canPrinter(id: number, kind: "print" | "control"): boolean }`
  - `function filterNavItems(items: NavItem[], opts: { printerEnabled: boolean; can: (p: Perm) => boolean }): NavItem[]`
  - Login error copy (exact):
    - `not_provisioned`: "Your account isn't set up here yet. Ask an admin to add you."
    - `inactive`: "This account has been deactivated."
    - `invalid_state`: "That sign-in link expired. Please try again."
    - `oidc_failed`: "Single sign-on failed. Try again or use your password."
    - Unknown codes use the `oidc_failed` copy.

- [ ] **Step 1: Write the failing tests**

```ts
// permissions.test.ts
it("admin can everything", () => expect(can(adminMe, "billing:manage")).toBe(true));
it("viewer cannot write", () => expect(can(viewerMe, "library:write")).toBe(false));
it("canPrinter needs perm and acl", () => {
  const me = { ...memberMe, permissions: ["printers:print"], printer_access: { "3": { print: true, control: false } } };
  expect(canPrinter(me, 3, "print")).toBe(true);
  expect(canPrinter(me, 3, "control")).toBe(false);
  expect(canPrinter(me, 4, "print")).toBe(false);
  expect(canPrinter(adminMe, 99, "control")).toBe(true);
});
// navItems.test.ts
it("hides perm-gated items the user lacks", () => {
  const items = filterNavItems(NAV_GROUPS[1].items, { printerEnabled: true, can: () => false });
  expect(items.map((i) => i.to)).not.toContain("/duplicates");
});
// LoginPage.test.tsx (mock api.get for /auth/oidc/providers -> [{slug:"authentik",name:"Authentik"}])
it("renders a provider button linking to start", async () => {
  expect(await screen.findByRole("link", { name: /sign in with authentik/i }))
    .toHaveAttribute("href", "/api/auth/oidc/authentik/start");
});
it("shows the error banner from ?error=", async () => {   // initialEntries ["/login?error=not_provisioned"]
  expect(await screen.findByRole("alert")).toHaveTextContent("Your account isn't set up here yet. Ask an admin to add you.");
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && npx vitest run src/lib/permissions.test.ts src/components/shell/navItems.test.ts src/pages/LoginPage.test.tsx`
Expected: FAIL, `Cannot find module '@/lib/permissions'` and the provider link is missing

- [ ] **Step 3: Implement the interfaces above**

The provider buttons are plain `<a href>` elements (full-page navigation, not a router `Link`). The local form stays below them, with a divider shown only when providers exist.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && npx vitest run && npm run build && npx oxlint`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/src
git commit -m "feat(web): add permission helpers, nav gating and SSO login buttons"
```

---

### Task 13: Web admin Settings: Users, Groups (+ printer access matrix), OIDC providers

**Files:**
- Create: `web/src/api/admin.ts`, `web/src/components/settings/UsersCard.tsx` (+ `.test.tsx`), `GroupsCard.tsx` (+ `.test.tsx`), `OidcProvidersCard.tsx` (+ `.test.tsx`)
- Modify: `web/src/api/types.ts` (`UserAdminOut`, `GroupOut`, `OidcProviderOut` and their inputs, mirroring Tasks 8–10), `web/src/pages/SettingsPage.tsx`

**Interfaces:**
- Consumes: Tasks 8–10 endpoints; `useAuth().can` (Task 12); `usePrinters()` from `@/api/printers` for the matrix rows
- Produces:
  - `useAdminUsers/useCreateUser/usePatchUser/useResetUserPassword/useDeleteUser`, `useAdminGroups/useCreateGroup/usePatchGroup/useDeleteGroup/useSetGroupMembers/useSetGroupPrinterAccess`, `useOidcProviders` (admin list, distinct from Task 12's public list: name it `useAdminOidcProviders`), `useCreateOidcProvider/usePatchOidcProvider/useDeleteOidcProvider`. Query keys are under `["admin", ...]`.
  - SettingsPage: a new `TabsTrigger value="access"` "Access", rendered only when `can("admin")`, containing `UsersCard`, `GroupsCard` and `OidcProvidersCard` stacked in `space-y-6`. The Accounts grid is left untouched, preserving the packing rule in its comment. The Storage and Printer tabs render only when `can("settings:write")`. `ChangePasswordCard` renders only when `me.has_password`.
  - GroupsCard printer matrix: rows = printers, columns = Print / Control / Requires review checkboxes. Saving sends one `PUT .../printer-access` with the full list.
  - OidcProvidersCard: the secret input is always empty and its placeholder reads "Leave blank to keep current secret" when `has_client_secret`. It shows the callback URL `${window.location.origin}/api/auth/oidc/${slug}/callback` with a copy button.

- [ ] **Step 1: Write the failing tests**

```ts
// UsersCard.test.tsx
it("lists users with role and SSO badge", async () => { /* get -> [{username:"bob",role:"member",oidc_linked:true,...}] */
  expect(await screen.findByText("bob")).toBeInTheDocument(); expect(screen.getByText("SSO")).toBeInTheDocument(); });
it("shows the 409 detail when demoting the last admin", async () => { /* patch rejects ApiError(409,"cannot remove the last admin") */
  expect(await screen.findByRole("alert")).toHaveTextContent("cannot remove the last admin"); });
// GroupsCard.test.tsx
it("saves the printer access matrix as one PUT", async () => {
  // toggle Print on printer 1, Save
  expect(putMock).toHaveBeenCalledWith("/admin/groups/5/printer-access",
    [{ printer_id: 1, can_print: true, can_control: false, requires_review: false }]); });
// OidcProvidersCard.test.tsx
it("omits client_secret from PATCH when left blank", async () => {
  expect(patchMock.mock.calls[0][1]).not.toHaveProperty("client_secret"); });
it("shows the callback URL", async () => {
  expect(await screen.findByText(/\/api\/auth\/oidc\/authentik\/callback$/)).toBeInTheDocument(); });
// SettingsPage: Access tab absent for a member (can("admin") false), present for admin
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && npx vitest run src/components/settings/UsersCard.test.tsx src/components/settings/GroupsCard.test.tsx src/components/settings/OidcProvidersCard.test.tsx`
Expected: FAIL with `Failed to resolve import "@/components/settings/UsersCard"`

- [ ] **Step 3: Implement the cards and hooks following the `BrowserExtensionCard` pattern (Card/CardHeader/CardContent, `ApiError.detail` in a `role="alert"`)**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && npx vitest run && npm run build && npx oxlint`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/src
git commit -m "feat(web): add admin access settings for users, groups and OIDC providers"
```

---

**Phase verification (after Task 13):** run the spec's §Verification block: full backend pytest + ruff + `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`, then the web build, oxlint and vitest. Manual drill: Authentik login as a mapped-group user shows `role: "admin"` in `/auth/me`, and removing them from the group drops admin on the next login. A viewer account gets 403 on writes. An extension token still imports.

### Critical Files for Implementation
- /home/OLYMPOS/jagannath/projects/3d-model-manager/backend/app/api/deps.py
- /home/OLYMPOS/jagannath/projects/3d-model-manager/backend/app/models/auth.py
- /home/OLYMPOS/jagannath/projects/3d-model-manager/backend/app/api/__init__.py
- /home/OLYMPOS/jagannath/projects/3d-model-manager/backend/tests/test_auth_sweep.py
- /home/OLYMPOS/jagannath/projects/3d-model-manager/web/src/api/auth.ts