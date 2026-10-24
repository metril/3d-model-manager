"""Sandboxed Jinja rendering for notification titles/bodies (Q6)."""

from __future__ import annotations

from datetime import UTC, datetime

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from app.services.notify.catalog import default_template

_env = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined)


def render_strict(title_tpl: str, body_tpl: str, ctx: dict) -> tuple[str, str]:
    """Raises ``TemplateError`` on syntax errors, undefined names or sandbox violations."""
    return _env.from_string(title_tpl).render(ctx), _env.from_string(body_tpl).render(ctx)


def render(event: str, title_tpl: str | None, body_tpl: str | None, ctx: dict) -> tuple[str, str]:
    """Render a custom template, falling back to the event default on any error."""
    if title_tpl is not None and body_tpl is not None:
        try:
            return render_strict(title_tpl, body_tpl, ctx)
        except (TemplateError, TypeError, ValueError, ArithmeticError):
            pass
    try:
        return render_strict(*default_template(event), ctx)
    except (TemplateError, TypeError, ValueError):
        return event, event


def base_context(
    event: str, printer_id: int | None, printer_name: str | None, payload: dict
) -> dict:
    return {
        **payload,
        "event": event,
        "printer_id": printer_id,
        "printer_name": printer_name or "Printer",
        "now": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
    }
