"""Bambu model codes (SSDP ``DevModel``) -> product name and serial prefix."""

from __future__ import annotations

import secrets

MODELS: dict[str, str] = {
    "N1": "A1 mini",
    "N2S": "A1",
    "C11": "P1P",
    "C12": "P1S",
    "N7": "P2S",
    "BL-P001": "X1 Carbon",
    "BL-P002": "X1",
    "C13": "X1E",
    "O1D": "H2D",
    "O1S": "H2S",
    "O1C": "H2C",
}

# 6-char serial prefix per model (real serials are 15 chars: prefix + 9).
SERIAL_PREFIXES: dict[str, str] = {
    "N1": "03000A",
    "N2S": "03900A",
    "C11": "01S00A",
    "C12": "01P00A",
    "N7": "22E00A",
    "BL-P001": "00M00A",
    "BL-P002": "00M00A",
    "C13": "03W00A",
    "O1D": "09400A",
    "O1S": "09400A",
    "O1C": "09400A",
}


def product_name(model: str) -> str:
    return MODELS.get(model, model)


def new_serial(model: str) -> str:
    prefix = SERIAL_PREFIXES.get(model, "00M09A")
    return prefix + "".join(secrets.choice("0123456789") for _ in range(9))


def new_access_code() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(8))
