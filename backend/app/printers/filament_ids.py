"""Bambu generic filament ids (``tray_info_idx``) by material.

A slot configured with a material but an empty id gets the printer's "unknown
filament" treatment (no K-profile match, wrong temps); the generic id for the
material is the safe default. Mirrors the table the web Configure dialog uses.
"""

from __future__ import annotations

GENERIC_FILAMENT_IDS: dict[str, str] = {
    "PLA": "GFL99",
    "PETG": "GFG99",
    "ABS": "GFB99",
    "ASA": "GFB98",
    "PC": "GFC99",
    "PA": "GFN99",
    "NYLON": "GFN99",
    "TPU": "GFU99",
    "PVA": "GFS99",
    "HIPS": "GFS98",
    "PLA-CF": "GFL98",
    "PETG-CF": "GFG98",
    "PA-CF": "GFN98",
    "PETG HF": "GFG96",
    "PLA SILK": "GFL96",
    "PLA HIGH SPEED": "GFL95",
    "PCTG": "GFG97",
    "PE": "GFP99",
    "PP": "GFP97",
}


def generic_filament_id(material: str | None) -> str:
    """The generic id for ``material`` (case/space-insensitive), or ``""``."""
    return GENERIC_FILAMENT_IDS.get(" ".join((material or "").upper().split()), "")
