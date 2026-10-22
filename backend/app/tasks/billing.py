"""Nightly balance recompute (P7 review/billing plan Task 7): beat ticks
``recompute_balances`` at 00:05 UTC. Reconciles ``cost_centers.balance``
against the ledger (``app.services.billing.recompute`` -- a no-op when no
cost center exists), logs a warning for every center whose cached balance
had drifted, and then re-attempts every ``over_budget``-held item
(``billing.retry_over_budget``) -- the mechanism that actually unholds an
item on a monthly-cap rollover, since a new month resets ``figures()``'s
``month_spent`` window.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.config import get_settings
from app.models.library import PrintQueueEntry
from app.services import billing
from app.services.app_config import get_app_config_sync
from app.tasks import base
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.billing.recompute_balances")
def recompute_balances(now: datetime | None = None) -> dict:
    """``now`` defaults to the real current time -- tests inject it to
    exercise a monthly rollover without waiting for the calendar to turn
    over. Returns ``{"drifted": {cc_id: {"old": ..., "new": ...}},
    "unheld": <count>}``.
    """
    now = now or datetime.now(UTC)
    settings = get_settings()
    with base.sync_session() as s:
        diffs = billing.recompute(s)
        drifted = {}
        for cc_id, (old, new) in diffs.items():
            if old != new:
                drifted[cc_id] = {"old": float(old), "new": float(new)}
                logger.warning(
                    "cost center %s balance drifted: cached %s -> recomputed %s", cc_id, old, new
                )

        cfg = get_app_config_sync(s, settings)
        held_center_ids = set(
            s.execute(
                select(PrintQueueEntry.cost_center_id)
                .where(
                    PrintQueueEntry.hold_reason == billing.HOLD_OVER_BUDGET,
                    PrintQueueEntry.cost_center_id.is_not(None),
                )
                .distinct()
            )
            .scalars()
            .all()
        )
        unheld = sum(billing.retry_over_budget(s, cfg, cc_id, now=now) for cc_id in held_center_ids)
        s.commit()

    return {"drifted": drifted, "unheld": unheld}
