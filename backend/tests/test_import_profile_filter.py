import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import Printer
from app.models.enums import ImportState, PrinterKind
from app.models.library import File
from app.models.system import Import
from app.services import app_config
from app.tasks.importing import import_from_url
from tests import corpus

pytestmark = pytest.mark.usefixtures("redis_url")


def _meta(code, product):
    compat = [{"model": code, "product": product, "nozzle": 0.4}]
    return {"compat": compat, "plates": [{"index": 1}]}


def _setup(fake):
    fake.files = {"p1s.stl": corpus.box_stl(), "a1.stl": corpus.box_stl()}
    fake.file_meta = {"p1s.stl": _meta("C12", "P1S"), "a1.stl": _meta("N2S", "A1")}


async def _run(db_session, override=None):
    from app.models.enums import ImportSite

    imp = Import(
        url="https://fake.test/thing/42",
        site=ImportSite.THINGIVERSE,
        external_id="42",
        state=ImportState.PENDING,
        meta={"profile_filter": override} if override else None,
    )
    db_session.add(imp)
    await db_session.commit()
    await db_session.refresh(imp)
    import_from_url(imp.id)
    await db_session.refresh(imp)
    return imp


async def _config(db_session, mode):
    cfg = await app_config.get_app_config(db_session, get_settings())
    await app_config.set_app_config(
        db_session, get_settings(), cfg.model_copy(update={"makerworld_profiles": mode})
    )


async def _printer(db_session, model):
    db_session.add(
        Printer(
            name="p",
            kind=PrinterKind.BAMBU_LAN,
            host="h",
            serial="s",
            access_code_enc="x",
            model=model,
            enabled=True,
        )
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_default_all_keeps_all_and_saves_source_meta(
    db_session, library_root, data_dir, fake_import
):
    _setup(fake_import)
    await _printer(db_session, "A1")
    imp = await _run(db_session)
    assert imp.state == ImportState.DONE
    assert imp.meta["profiles_total"] == 2 and imp.meta["profiles_kept"] == 2
    rows = (await db_session.execute(select(File))).scalars().all()
    by = {f.rel_path: f.source_meta for f in rows}
    assert by["p1s.stl"]["compat"][0]["product"] == "P1S"
    assert by["a1.stl"]["plates"] == [{"index": 1}]


@pytest.mark.asyncio
async def test_setting_owned_filters(db_session, library_root, data_dir, fake_import):
    _setup(fake_import)
    await _printer(db_session, "A1")
    await _config(db_session, "owned")
    imp = await _run(db_session)
    assert imp.state == ImportState.DONE
    assert imp.meta["profiles_total"] == 2 and imp.meta["profiles_kept"] == 1
    assert imp.meta["files"] == ["a1.stl"]


@pytest.mark.asyncio
async def test_override_beats_setting(db_session, library_root, data_dir, fake_import):
    _setup(fake_import)
    await _printer(db_session, "A1")
    await _config(db_session, "owned")
    imp = await _run(db_session, override="all")
    assert imp.meta["profile_filter"] == "all"
    assert imp.meta["profiles_kept"] == 2
    assert sorted(imp.meta["files"]) == ["a1.stl", "p1s.stl"]


@pytest.mark.asyncio
async def test_override_owned_beats_all_setting(db_session, library_root, data_dir, fake_import):
    _setup(fake_import)
    await _printer(db_session, "P1S")
    imp = await _run(db_session, override="owned")
    assert imp.meta["files"] == ["p1s.stl"]
