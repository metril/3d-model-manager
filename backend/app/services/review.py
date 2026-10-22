"""Review gate (P7 review/billing plan, Task 3): whether a queue entry
needs a reviewer before it can dispatch, and whether a given user may act
as that reviewer. SYNC world (``app.tasks.base``) twin alongside
``app.services.permissions``' own sync helpers -- both the async enqueue
path (via ``AsyncSession.run_sync``) and the worker-side dispatcher reach
this through a plain ``Session``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.api.deps import AuthContext
from app.models.library import PrintQueueEntry
from app.models.slicing import PrintRecipe
from app.services.permissions import printer_acl_sync


def needs_review(ctx: AuthContext, targets: set[int] | None) -> bool:
    """Whether ``ctx`` (the user about to enqueue) must go through review
    for the given target printer(s). An admin or anyone who can review
    their own work (``queue:review``) never needs review. ``targets=None``
    (no specific printer picked yet) fans out over every printer in the
    user's own ACL they can print to -- any of those requiring review is
    enough to hold the item.
    """
    if ctx.is_admin or "queue:review" in ctx.perms:
        return False
    if targets is None:
        targets = {pid for pid, acl in ctx.printer_acl.items() if acl.can_print}
    return any(ctx.printer_acl[p].requires_review for p in targets if p in ctx.printer_acl)


def item_targets(s: Session, item: PrintQueueEntry) -> set[int] | None:
    """The queue entry's own known printer target(s): its explicit
    ``printer_id``, else its recipe's ``target_printer_id``, else ``None``
    (no specific target yet -- any compatible printer).
    """
    if item.printer_id is not None:
        return {item.printer_id}
    if item.recipe_id is not None:
        recipe = s.get(PrintRecipe, item.recipe_id)
        if recipe is not None and recipe.target_printer_id is not None:
            return {recipe.target_printer_id}
    return None


def review_printers(s: Session, item: PrintQueueEntry) -> set[int]:
    """The printer(s) that made ``item`` need review in the first place,
    from its CREATOR's own merged ACL -- the set a reviewer's own ACL is
    checked against in ``can_review``."""
    acl = printer_acl_sync(s, item.created_by_user_id)
    triggers = {pid for pid, a in acl.items() if a.can_print and a.requires_review}
    targets = item_targets(s, item)
    if targets is None:
        return triggers
    return triggers & targets


def can_review(ctx: AuthContext, s: Session, item: PrintQueueEntry) -> bool:
    """Whether ``ctx`` may approve/reject ``item``: an admin always can; a
    ``queue:review`` holder can only for a printer that's actually one of
    this item's review triggers AND that they themselves can print to.
    """
    if ctx.is_admin:
        return True
    if "queue:review" not in ctx.perms:
        return False
    return any(
        ctx.printer_acl.get(p) is not None and ctx.printer_acl[p].can_print
        for p in review_printers(s, item)
    )
