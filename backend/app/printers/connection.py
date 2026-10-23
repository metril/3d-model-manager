"""Builds a :class:`~app.printers.base.PrinterConnection` from a persisted
``Printer`` row, decrypting ``access_code_enc`` HERE -- the single
adapter-build seam (never in a CRUD read path; see ``app.crypto``).
"""

from __future__ import annotations

from app.config import Settings
from app.crypto import decrypt_secret
from app.models import Printer
from app.printers.base import PrinterConnection
from app.printers.camera_select import decrypt_options


def connection_from_printer(settings: Settings, printer: Printer) -> PrinterConnection:
    return PrinterConnection(
        host=printer.host,
        serial=printer.serial,
        access_code=decrypt_secret(settings, printer.access_code_enc),
        model=printer.model,
        options=decrypt_options(settings, printer.options),
    )
