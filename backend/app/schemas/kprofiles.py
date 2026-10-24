"""T2 K-profile request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class KProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    filament_id: str = Field(min_length=1, max_length=32)
    k_value: float = Field(ge=0, le=2)
    n_coef: float = Field(default=0, ge=0, le=2)
    nozzle_diameter: str = "0.4"
    extruder_id: int = Field(default=0, ge=0, le=1)
    nozzle_id: str | None = Field(default=None, max_length=32)
    setting_id: str | None = Field(default=None, max_length=40)  # present = edit
    cali_idx: int | None = Field(default=None, ge=0, le=4095)


class KProfileSelectIn(BaseModel):
    ams_id: int
    tray_id: int
    cali_idx: int = Field(ge=-1, le=4095)
    filament_id: str = Field(min_length=1, max_length=32)
    nozzle_diameter: str = "0.4"


class KProfileNoteIn(BaseModel):
    note: str = Field(min_length=1, max_length=500)


class SpoolKProfileIO(BaseModel):
    """One stored profile link, used as both PUT body item and GET row."""

    model_config = ConfigDict(from_attributes=True)

    printer_id: int
    extruder: int = Field(default=0, ge=0, le=1)
    nozzle_diameter: float = Field(ge=0.1, le=2.0)
    nozzle_type: str | None = Field(default=None, max_length=64)
    k_value: float = Field(ge=0, le=2)
    n_coef: float | None = Field(default=None, ge=0, le=2)
    name: str | None = Field(default=None, max_length=64)
    cali_idx: int | None = Field(default=None, ge=0, le=4095)
    setting_id: str | None = Field(default=None, max_length=40)
