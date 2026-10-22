"""Fixtures for the archive service tests. Registered from conftest.py."""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crypto import encrypt_secret
from app.models import File, Model, Printer, PrintJob, Revision, Spool, User
from app.models.enums import (
    BlobFormat,
    BlobKind,
    PrinterKind,
    PrintJobOrigin,
    PrintJobState,
)
from app.models.library import Blob
from app.services.spool_inventory import assign_slot, split_tray
from app.tasks.base import sync_session

CORPUS = Path(__file__).parent / "corpus_real"
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def sliced_3mf_bytes() -> bytes:
    return (CORPUS / "DotC parts - Part 1_plate_1.gcode.3mf").read_bytes()


def _with_extra_filament(data: bytes, filament_xml: str) -> bytes:
    """``data`` (a real sliced ``.gcode.3mf``) with ``filament_xml`` spliced
    into its single plate's ``slice_info.config``, every other zip entry
    byte-for-byte unchanged."""
    with zipfile.ZipFile(BytesIO(data)) as zin:
        items = {name: zin.read(name) for name in zin.namelist()}
    text = items["Metadata/slice_info.config"].decode()
    items["Metadata/slice_info.config"] = text.replace(
        "</plate>", f"{filament_xml}</plate>", 1
    ).encode()
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, content in items.items():
            zout.writestr(name, content)
    return out.getvalue()


@pytest.fixture
def two_filament_3mf_bytes(sliced_3mf_bytes: bytes) -> bytes:
    """``sliced_3mf_bytes`` (one filament, id 1) with a second filament (id
    3) added to the same plate -- the filament-id-keyed tray mapping
    (``app.services.archive``) needs >=2 filaments on one plate to exercise
    the non-positional id lookup."""
    extra = (
        '<filament id="3" tray_info_idx="GFSNL05" type="PETG" color="#00FF00" '
        'used_m="5.00" used_g="15.00" group_id="0" nozzle_diameter="0.40" '
        'volume_type="Standard" used_for_object="true" used_for_support="false"/>'
    )
    return _with_extra_filament(sliced_3mf_bytes, extra)


@pytest.fixture
def legacy_meta_3mf_bytes(sliced_3mf_bytes: bytes) -> bytes:
    """``sliced_3mf_bytes`` with its single filament's ``id`` attribute
    stripped -- simulates meta parsed pre-P5 (``filament_id`` didn't exist
    yet, so ``parse_gcode_3mf`` reads it back as ``None``)."""
    with zipfile.ZipFile(BytesIO(sliced_3mf_bytes)) as zin:
        items = {name: zin.read(name) for name in zin.namelist()}
    text = items["Metadata/slice_info.config"].decode()
    items["Metadata/slice_info.config"] = text.replace('<filament id="1" ', "<filament ").encode()
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, content in items.items():
            zout.writestr(name, content)
    return out.getvalue()


@pytest.fixture
def fifty_gram_3mf_bytes(sliced_3mf_bytes: bytes) -> bytes:
    """``sliced_3mf_bytes`` with its single filament's ``used_g`` swapped to
    exactly 50g -- a round number for the per-spool pricing tests."""
    with zipfile.ZipFile(BytesIO(sliced_3mf_bytes)) as zin:
        items = {name: zin.read(name) for name in zin.namelist()}
    text = items["Metadata/slice_info.config"].decode()
    items["Metadata/slice_info.config"] = text.replace('used_g="61.64"', 'used_g="50.00"').encode()
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, content in items.items():
            zout.writestr(name, content)
    return out.getvalue()


def spool_on_tray(session: Session, printer_id: int, tray: int, **kwargs) -> int:
    """Register a spool and assign it to ``printer_id``'s global AMS
    ``tray`` (P5 spool tray plan). Returns the spool id."""
    kwargs.setdefault("material_type", "PLA")
    kwargs.setdefault("label_weight_g", 1000.0)
    kwargs.setdefault("weight_used_g", 0.0)
    kwargs.setdefault("low_stock_g", 100.0)
    spool = Spool(**kwargs)
    session.add(spool)
    session.commit()
    unit, slot = split_tray(tray)
    assign_slot(session, printer_id, unit, slot, spool.id)
    return spool.id


class Seed:
    def __init__(self, session: Session) -> None:
        self.s = session
        self.printer_id: int | None = None

    def model(self, name: str) -> int:
        m = Model(slug=name.lower().replace(" ", "-"), name=name)
        self.s.add(m)
        self.s.commit()
        return m.id

    def user(self, username: str = "maker") -> int:
        u = User(username=username)
        self.s.add(u)
        self.s.commit()
        return u.id

    def file(self, name: str, blob_hash: str) -> tuple[int, int, int]:
        """(model_id, revision_id, file_id) with a library file on ``blob_hash``."""
        model_id = self.model(name)
        rev = Revision(model_id=model_id, number=1, dir_name="rev-001")
        self.s.add(rev)
        self.s.flush()
        if self.s.get(Blob, blob_hash) is None:
            self.s.add(
                Blob(hash=blob_hash, size=1, kind=BlobKind.SLICED, format=BlobFormat.GCODE_3MF)
            )
            self.s.flush()
        f = File(
            revision_id=rev.id, blob_hash=blob_hash, rel_path=f"{name}.gcode.3mf", storage_path="x"
        )
        self.s.add(f)
        self.s.commit()
        return model_id, rev.id, f.id

    def job(
        self,
        *,
        origin: str = "external",
        file_id: int | None = None,
        subtask: str | None = None,
        duration_s: int | None = 3600,
        layer: int | None = 474,
        total_layers: int | None = 474,
        plate: int | None = None,
        user_id: int | None = None,
    ) -> int:
        if self.printer_id is None:
            p = Printer(
                name="A1 Mini",
                kind=PrinterKind.BAMBU_LAN,
                host="10.0.0.5",
                serial="SN1",
                access_code_enc=encrypt_secret(get_settings(), "s3cretcode"),
            )
            self.s.add(p)
            self.s.flush()
            self.printer_id = p.id
        j = PrintJob(
            printer_id=self.printer_id,
            created_by_user_id=user_id,
            file_id=file_id,
            origin=PrintJobOrigin(origin),
            plate=plate,
            subtask_name=subtask,
            state=PrintJobState.FINISHED,
            layer=layer,
            total_layers=total_layers,
            started_at=T0 if duration_s is not None else None,
            finished_at=T0 + timedelta(seconds=duration_s) if duration_s is not None else None,
        )
        self.s.add(j)
        self.s.commit()
        return j.id


@pytest.fixture
def seed(data_dir: Path) -> Iterator[Seed]:
    with sync_session() as s:
        yield Seed(s)
