"""Schemas for the Home Assistant settings API (P8 Task 7): the connection
config (secrets write-only, the same ``"***"`` sentinel convention as
storage/import-token settings -- see ``app.api.settings``), the two
connection tests, and ``ha_links`` CRUD.

``alert_condition``'s shape (``app.models.home_assistant.HaLink``'s
docstring; P8 plan Task 1) is ``{"op": "eq"|"ne"|"gt"|"gte"|"lt"|"lte"|
"in", "value": str|float|list}`` -- validated here, independent of
``app.services.home_assistant.evaluate``'s own (currently narrower)
runtime support for it.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import HaRole
from app.services.ha_config import parse_mqtt_url

_NUMERIC_OPS = ("gt", "gte", "lt", "lte")
_SCALAR_OPS = ("eq", "ne")
_ALL_OPS = (*_SCALAR_OPS, *_NUMERIC_OPS, "in")

_ENTITY_ID_PATTERN = r"^[a-z_]+\.[a-z0-9_]+$"


def validate_alert_condition(cond: dict | None) -> dict | None:
    """Shared shape check for ``HaLinkIn``/``HaLinkPatchIn`` and the PATCH
    endpoint's merged-state revalidation (``app.api.home_assistant``)."""
    if cond is None:
        return None
    if not isinstance(cond, dict):
        raise ValueError("alert_condition must be an object")
    op = cond.get("op")
    if op not in _ALL_OPS:
        raise ValueError(f"alert_condition.op must be one of {_ALL_OPS}")
    if "value" not in cond:
        raise ValueError("alert_condition.value is required")
    value = cond["value"]
    if op in _NUMERIC_OPS:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"alert_condition op {op!r} needs a numeric value")
    elif op == "in":
        if not isinstance(value, list):
            raise ValueError('alert_condition op "in" needs a list value')
    else:  # eq/ne
        if isinstance(value, (list, dict)):
            raise ValueError(f"alert_condition op {op!r} needs a scalar value")
    return cond


def check_link_invariants(
    *,
    role: HaRole,
    printer_id: int | None,
    alert_condition: dict | None,
    hold_queue: bool,
    pause_print: bool,
) -> None:
    """Shared cross-field invariants for create (model_validator) and
    patch (endpoint, over the merged row): an ``energy_kwh`` link always
    names a printer; holding the queue or pausing a print needs an
    ``alert_condition`` to actually fire on."""
    if role == HaRole.ENERGY_KWH and printer_id is None:
        raise ValueError("role 'energy_kwh' requires printer_id")
    if (hold_queue or pause_print) and alert_condition is None:
        raise ValueError("hold_queue/pause_print requires an alert_condition")


# ---------------------------------------------------------------------------
# Connection config
# ---------------------------------------------------------------------------


class HaSettingsOut(BaseModel):
    ha_mqtt_url: str | None = None
    ha_mqtt_user: str | None = None
    has_mqtt_password: bool = False
    ha_discovery_prefix: str = "homeassistant"
    ha_url: str | None = None
    has_token: bool = False
    pull_status: dict | None = None
    bridge_status: str | None = None


class HaSettingsIn(BaseModel):
    """All fields optional (sentinel-merge, same rules as ``HaConfig``'s
    ``set_ha_config``): an omitted field keeps the stored value; for a
    secret field, ``"***"``/omitted keeps, ``""`` clears, anything else
    (re)encrypts. Also reused, unsaved, as the optional override body for
    the two connection-test endpoints.
    """

    ha_mqtt_url: str | None = None
    ha_mqtt_user: str | None = None
    ha_mqtt_password: str | None = None
    ha_discovery_prefix: str | None = None
    ha_url: str | None = None
    ha_token: str | None = None

    @field_validator("ha_mqtt_url")
    @classmethod
    def _check_mqtt_url(cls, v: str | None) -> str | None:
        if not v:
            return v
        try:
            parse_mqtt_url(v)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return v

    @field_validator("ha_url")
    @classmethod
    def _check_ha_url(cls, v: str | None) -> str | None:
        if not v:
            return v
        parsed = urlsplit(v)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("ha_url must be an absolute http(s) URL")
        return v


class HaTestOut(BaseModel):
    ok: bool
    detail: str


# ---------------------------------------------------------------------------
# Links CRUD
# ---------------------------------------------------------------------------


class HaLinkIn(BaseModel):
    printer_id: int | None = None
    entity_id: str = Field(pattern=_ENTITY_ID_PATTERN)
    role: HaRole
    alert_condition: dict | None = None
    hold_queue: bool = False
    pause_print: bool = False

    @field_validator("alert_condition")
    @classmethod
    def _check_alert_condition(cls, v: dict | None) -> dict | None:
        return validate_alert_condition(v)

    @model_validator(mode="after")
    def _check_invariants(self) -> HaLinkIn:
        check_link_invariants(
            role=self.role,
            printer_id=self.printer_id,
            alert_condition=self.alert_condition,
            hold_queue=self.hold_queue,
            pause_print=self.pause_print,
        )
        return self


class HaLinkPatchIn(BaseModel):
    """Partial update -- only fields explicitly sent are applied
    (``model_dump(exclude_unset=True)`` in the endpoint); the cross-field
    invariants are re-checked over the MERGED row, not this payload alone,
    since e.g. flipping just ``role`` to ``energy_kwh`` must still 422 if
    the stored ``printer_id`` is None.
    """

    printer_id: int | None = None
    entity_id: str | None = Field(default=None, pattern=_ENTITY_ID_PATTERN)
    role: HaRole | None = None
    alert_condition: dict | None = None
    hold_queue: bool | None = None
    pause_print: bool | None = None

    @field_validator("alert_condition")
    @classmethod
    def _check_alert_condition(cls, v: dict | None) -> dict | None:
        return validate_alert_condition(v)


class HaLinkOut(BaseModel):
    id: int
    printer_id: int | None
    printer_name: str | None
    entity_id: str
    role: HaRole
    alert_condition: dict | None
    hold_queue: bool
    pause_print: bool
    created_by_user_id: int | None
    created_at: datetime
    updated_at: datetime
