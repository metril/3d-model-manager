"""Shared test fixtures: a real Postgres testcontainer + the real migration.

Per the M1 global constraints, DB tests run against REAL PostgreSQL via
testcontainers -- never mocked, never SQLite. The schema is created by
applying the actual Alembic baseline migration (not
``Base.metadata.create_all``), so these tests also validate the migration
itself.
"""

import os
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import blake3
import httpx
import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from alembic import command
from app.config import get_settings
from app.db import get_engine, get_sessionmaker
from app.main import create_app
from app.models import Base, Blob, File, Model, Revision, User
from app.models.enums import BlobFormat, BlobKind, UserRole
from app.security import hash_password
from app.storage.local import LocalStorageBackend
from app.tasks.base import get_sync_engine, get_sync_sessionmaker
from tests import corpus as corpus_module
from tests.corpus import CorpusPaths

BACKEND_DIR = Path(__file__).resolve().parent.parent

# tests/storage_containers.py (M3 Task 2) supplies the MinIO/dperson-samba
# container fixtures + smb_backend/s3_backend factory fixtures consumed by
# tests/test_storage_contract.py and Task 3/4's backend-specific tests.
pytest_plugins = (
    "tests.storage_containers",
    "tests.printer_fixtures",
    "tests.archive_fixtures",
    "tests.importer_fixtures",
    "tests.migration_helpers",
    "tests.auth_fixtures",
    "tests.oidc_fixtures",
    "tests.billing_fixtures",
    "tests.ha_fixtures",
)

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "correct horse battery staple"

# gltfpack WASM shim (M2 Task 1: scripts/fetch-gltfpack.sh; consumed starting
# Task 5's optimize_glb step). Prepending its bin dir to PATH once here, at
# module import time (conftest.py is imported exactly once per test
# session), lets `Settings.gltfpack_path`'s default of plain `"gltfpack"`
# resolve via normal PATH lookup -- mirroring how it resolves in the Docker
# image, where gltfpack is source-built straight onto PATH. A no-op until
# `scripts/fetch-gltfpack.sh` has been run at least once.
_GLTFPACK_BIN_DIR = BACKEND_DIR / ".tools" / "node_modules" / ".bin"
if _GLTFPACK_BIN_DIR.is_dir():
    os.environ["PATH"] = f"{_GLTFPACK_BIN_DIR}{os.pathsep}{os.environ.get('PATH', '')}"


def _reset_settings_and_engine_caches() -> None:
    """``get_settings``/``get_engine``/``get_sessionmaker`` (API async world)
    and ``get_sync_engine``/``get_sync_sessionmaker`` (worker sync world, see
    ``app.tasks.base``) are all ``lru_cache``d process-wide singletons. Tests
    that repoint ``DATABASE_URL``/``REDIS_URL`` must clear all of
    them so fresh engines are built against the new URLs.
    """
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    get_sync_engine.cache_clear()
    get_sync_sessionmaker.cache_clear()


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """Start one Postgres container for the whole test session."""
    with PostgresContainer("postgres:16-alpine") as container:
        url = container.get_connection_url(driver="asyncpg")
        os.environ["DATABASE_URL"] = url
        _reset_settings_and_engine_caches()
        yield url
    del os.environ["DATABASE_URL"]
    _reset_settings_and_engine_caches()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    """Start one Redis container for the whole test session (Task 6: SSE +
    Celery job-event publishing). fakeredis is deliberately not used -- the
    M1 global constraints require real infra for integration tests.
    """
    with RedisContainer("redis:7-alpine") as container:
        url = f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(container.port)}/0"
        os.environ["REDIS_URL"] = url
        _reset_settings_and_engine_caches()
        yield url
    del os.environ["REDIS_URL"]
    _reset_settings_and_engine_caches()


@pytest.fixture(scope="session", autouse=True)
def _celery_eager_mode() -> None:
    """Run Celery tasks synchronously, in-process, for the whole test
    session (Task 6 interface decision) -- no worker process, no broker
    round trip; `.delay()`/`.apply_async()` just call the task body inline.
    `task_eager_propagates=True` so a genuinely unexpected exception inside a
    task surfaces as a normal test failure instead of only being recorded in
    the job's `error` column.
    """
    from app.tasks.celery_app import celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True


@pytest.fixture(scope="session")
def migrated_db(postgres_url: str) -> str:
    """Apply the real baseline Alembic migration against the container."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    command.upgrade(config, "head")
    return postgres_url


@pytest.fixture(autouse=True)
async def _truncate_all_tables(migrated_db: str) -> AsyncGenerator[None, None]:
    """Empty every app table before each test function runs."""
    table_names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
async def db_session(_truncate_all_tables: None) -> AsyncGenerator[AsyncSession, None]:
    """A function-scoped async session against the migrated testcontainer DB."""
    async with get_sessionmaker()() as session:
        yield session


@pytest.fixture
async def client(migrated_db: str, redis_url: str) -> AsyncGenerator[httpx.AsyncClient, None]:
    """An ASGI test client for the app, wired to the container DB + Redis."""
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Library-domain fixtures (Task 5): a real local storage backend rooted at a
# tmp_path, plus auth/seeding helpers shared by the models/revisions/
# tags/notes test modules.
# ---------------------------------------------------------------------------


@pytest.fixture
def library_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point ``LIBRARY_ROOT`` at a fresh tmp_path for this test.

    ``app.storage.registry.get_backend`` reads ``get_settings()`` fresh on
    every call (it's not baked into the app at ``create_app()`` time), so
    setting the env var and clearing the settings cache before the test body
    issues any HTTP request is sufficient -- fixture instantiation always
    completes before the test function body runs, regardless of the order
    ``client``/``library_root`` appear in a test's parameter list.
    """
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setenv("LIBRARY_ROOT", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


@pytest.fixture
def backend(library_root: Path) -> LocalStorageBackend:
    """A ``LocalStorageBackend`` on the same root the app is configured to
    use for this test (see ``library_root``).
    """
    return LocalStorageBackend(library_root)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point ``DATA_DIR`` (spool root, Task 6) at a fresh tmp_path for
    this test -- mirrors ``library_root`` above.
    """
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("DATA_DIR", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


@pytest.fixture
def printer_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Turn the printer feature flag on for this test, with a tmp
    ``DATA_DIR`` so the Fernet key (``app.crypto``) is isolated per
    test rather than shared/persisted across the suite.
    """
    pdata = tmp_path / "pdata"
    pdata.mkdir()
    monkeypatch.setenv("PRINTER_ENABLED", "true")
    monkeypatch.setenv("DATA_DIR", str(pdata))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        username=ADMIN_USERNAME, password_hash=hash_password(ADMIN_PASSWORD), role=UserRole.ADMIN
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def authenticated_client(client: httpx.AsyncClient, admin_user: User) -> httpx.AsyncClient:
    """The ``client`` fixture, already logged in as the seeded admin user.

    httpx's ``AsyncClient`` keeps a cookie jar, so every subsequent request
    on the same client instance carries the session cookie automatically.
    """
    response = await client.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 204
    return client


@pytest.fixture
def seed_file(
    db_session: AsyncSession, backend: LocalStorageBackend
) -> Callable[..., Awaitable[File]]:
    """Factory to plant a file directly (writing bytes via the backend +
    inserting Blob/File rows) without going through the upload endpoint
    (Task 6). Task 5's interface decision calls for exactly this: tests seed
    revision contents by hand.
    """

    async def _seed(
        model: Model,
        revision: Revision,
        rel_path: str,
        content: bytes,
        *,
        blob_format: BlobFormat = BlobFormat.STL,
        blob_kind: BlobKind = BlobKind.MESH,
    ) -> File:
        digest = blake3.blake3(content).hexdigest()
        storage_path = f"{model.slug}/{revision.dir_name}/{rel_path}"
        backend.write(storage_path, [content])

        blob = await db_session.get(Blob, digest)
        if blob is None:
            blob = Blob(hash=digest, size=len(content), kind=blob_kind, format=blob_format)
            db_session.add(blob)
            await db_session.flush()

        file = File(
            revision_id=revision.id,
            blob_hash=digest,
            rel_path=rel_path,
            storage_path=storage_path,
            verified_at=datetime.now(UTC),
        )
        db_session.add(file)
        await db_session.commit()
        await db_session.refresh(file)
        return file

    return _seed


# ---------------------------------------------------------------------------
# Processing-pipeline fixtures (M2 Task 1): a real on-disk copy of every
# ``tests.corpus`` builder, written once for the whole session.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> CorpusPaths:
    """Write every ``tests.corpus`` fixture to disk once per session (all
    builders are deterministic and pure, so sharing one copy across every
    test that needs it is safe)."""
    root = tmp_path_factory.mktemp("corpus")

    box_stl = root / "box.stl"
    box_stl.write_bytes(corpus_module.box_stl())
    box_obj = root / "box.obj"
    box_obj.write_bytes(corpus_module.box_obj())
    box_3mf_generic = root / "box_generic.3mf"
    box_3mf_generic.write_bytes(corpus_module.box_3mf_generic())
    box_3mf_meter = root / "box_meter.3mf"
    box_3mf_meter.write_bytes(corpus_module.box_3mf_meter())
    box_3mf_bambu = root / "box_bambu.3mf"
    box_3mf_bambu.write_bytes(corpus_module.box_3mf_bambu())
    box_3mf_bambu_with_thumb = root / "box_bambu_with_thumb.3mf"
    box_3mf_bambu_with_thumb.write_bytes(corpus_module.box_3mf_bambu_with_thumb())
    sliced_gcode_3mf = root / "sliced.gcode.3mf"
    sliced_gcode_3mf.write_bytes(corpus_module.sliced_gcode_3mf())
    sliced_gcode_3mf_missing_index = root / "sliced_missing_index.gcode.3mf"
    sliced_gcode_3mf_missing_index.write_bytes(corpus_module.sliced_gcode_3mf_missing_index())
    bambu_gcode = root / "plate_1.gcode"
    bambu_gcode.write_bytes(corpus_module.bambu_gcode())
    box_step = root / "box.step"
    corpus_module.box_step(box_step)
    box_iges = root / "box.iges"
    corpus_module.box_iges(box_iges)
    red_png = root / "red.png"
    red_png.write_bytes(corpus_module.red_png())
    red_webp = root / "red.webp"
    red_webp.write_bytes(corpus_module.red_webp())

    return CorpusPaths(
        box_stl=box_stl,
        box_obj=box_obj,
        box_3mf_generic=box_3mf_generic,
        box_3mf_meter=box_3mf_meter,
        box_3mf_bambu=box_3mf_bambu,
        box_3mf_bambu_with_thumb=box_3mf_bambu_with_thumb,
        sliced_gcode_3mf=sliced_gcode_3mf,
        sliced_gcode_3mf_missing_index=sliced_gcode_3mf_missing_index,
        bambu_gcode=bambu_gcode,
        box_step=box_step,
        box_iges=box_iges,
        red_png=red_png,
        red_webp=red_webp,
    )
