"""Fixtures for the archive service tests. Registered from conftest.py."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crypto import encrypt_secret
from app.models import File, Model, Printer, PrintJob, Revision, User
from app.models.enums import (
    BlobFormat,
    BlobKind,
    PrinterKind,
    PrintJobOrigin,
    PrintJobState,
)
from app.models.library import Blob
from app.tasks.base import sync_session

CORPUS = Path(__file__).parent / "corpus_real"
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def sliced_3mf_bytes() -> bytes:
    return (CORPUS / "DotC parts - Part 1_plate_1.gcode.3mf").read_bytes()


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
