# P7 Job Review, Cost Centers and Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (or superpowers:executing-plans) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Queue items on a `requires_review` printer wait for a reviewer's approve or reject. When billing is on, every per-file queue item draws on a cost center. It reserves its estimated cost at queue time, or is held `over_budget` if the cost does not fit. At archive the reservation is released and the actual `prints.total_cost` is charged, through an append-only, idempotent ledger. Deposits and adjustments need `billing:manage`. A nightly job recomputes the balance cache from the ledger.

**Architecture:** `services/billing.py` is sync-only and owns the ledger: append, figures, `reserve`/`release`/`charge`/`transfer`, private centers, recompute and over-budget retry. Async routes call it through `await db.run_sync(...)`. It never commits; the caller commits, which releases the `FOR UPDATE` row lock. `services/review.py` holds the pure `needs_review(ctx, targets)` and the sync `review_printers`/`can_review`. Hooks:
- `queue.enqueue_file`: review state, cost center and reservation.
- `tasks/slicing.py` READY: reservation for recipe items.
- `dispatcher._dispatch`: transfers a reservation to each clone.
- `dispatcher.eligible_queue_items`: the review/hold filter, which is the only filter change.
- `queue.remove_entry`: release.
- `archive.archive_job`: release, then charge.
- `print_plan.create_job`: 409 `queue_required`.

New routers are `api/finance.py` and the review routes in `api/queue.py`. Beat `billing-recompute` is additive.

**Tech Stack:** FastAPI + SQLAlchemy 2 (async + sync, `run_sync`), Postgres `NUMERIC(12,2)` + `SELECT … FOR UPDATE` + `INSERT … ON CONFLICT DO NOTHING`, Alembic, Celery beat `crontab`, pytest + testcontainers; React 19, TanStack Query/Router, shadcn/ui, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-21-bambuddy-parity-design.md` §P7. Also see §Integration rule (`created_by_user_id` on user-created rows, NULL from beat; queueing = `printers:print` + review state), §P1 perms (`queue:review`, `billing:manage`, `printer_access.requires_review`) and the P6 seam (`dispatcher.eligible_queue_items` is the ONE filter P7 extends). P8 hook: `prints.energy_cost` already feeds `total_cost`, so P8 energy flows into charges with no billing change.

**Branch:** `p7-billing` from `main` after P6 merges. Never commit the user's `compose.yaml` hunk (no task touches compose). Agents never `git stash`/`checkout`.

## Global Constraints

- Commit: `git -c user.name=metril -c user.email=1517921+metril@users.noreply.github.com commit -m "<type(scope): subject>"`. No trailers, no AI mention.
- **Test discipline:** implementers run ONLY the task's targeted files; the controller runs the full suite. Backend: `cd backend && timeout 600 uv run pytest tests/<file> -q`, then `uv run ruff check . && uv run ruff format --check .`. Web: `cd web && npx vitest run <file>`, then `npm run build` (`tsc --noEmit` is a no-op) and `npx oxlint` (no new warnings vs baseline).
- Migration: one revision `d2a7c4e9f1b3_p7_billing`, `down_revision = "b4d7e2a9c6f1"`.
- Enums (`models/enums.py`): `LedgerKind = charge|deposit|adjustment|reservation|release`; `ReviewState = none|pending|approved|rejected`. Constant `HOLD_OVER_BUDGET = "over_budget"` (`services/billing.py`); `hold_reason` is plain TEXT.
- Signs: deposit `+`; adjustment `±` (non-zero); charge `−total_cost`; reservation `−est`; release `+reserved` (exact negation of the item's reservation). Amounts are `Decimal` quantized with `q2(x) = Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP)`.
- `balance` (the cache) = Σ amount over `{deposit, adjustment, charge}`. `open_reserved` = −Σ amount over `{reservation, release}`. `month_spent` = −Σ charge with `created_at >= first of the current month 00:00 UTC`. `budget_total` = allowance/credit line (NULL = unlimited funds); `budget_monthly` NULL = no monthly cap. New private centers are created with both NULL (unlimited), so turning billing on holds nothing until an admin sets limits.
- Idempotency: unique partial index `uq_ledger_queue_kind ON ledger_entries(print_queue_id, kind) WHERE print_queue_id IS NOT NULL`. Every queue-keyed append is `ON CONFLICT DO NOTHING`, and a duplicate is a silent no-op.
- FKs: `cost_centers.owner_user_id` → users CASCADE; `group_id` → groups CASCADE. `CHECK ((owner_user_id IS NULL) <> (group_id IS NULL))`. Partial unique `uq_cost_centers_owner ON (owner_user_id) WHERE owner_user_id IS NOT NULL` (one private center per user). `ledger_entries.cost_center_id` → CASCADE; `print_queue_id` → SET NULL; `print_id` → prints SET NULL; `created_by_user_id` → SET NULL. `print_queue.cost_center_id` → RESTRICT; `reviewed_by` → users SET NULL.
- `AppConfig.billing_enabled: bool = False` (no env; follows the `filament_cost_per_kg` pattern).
- Billing gates CREATION: assigning a center, reserving and holding. SETTLEMENT (release/charge) runs for any item that already has a reservation row, so turning billing off mid-flight never leaks reservations. Turning billing off also clears every `hold_reason='over_budget'`.
- Review is independent of billing. It is evaluated only for per-file items (`file_id` set), because bare entries never dispatch and get `none`.
- Release triggers: reject, `DELETE /queue/{id}`, and archive of a job that started (`started_at IS NOT NULL`). Terminal `failed`/`held` keep their reservation, because a manual retry must not need a second reservation key.
- Web query keys: `["finance", "config"]`, `["finance", "cost-centers"]`, `["finance", "ledger", id]`, `["queue", "review-inbox"]`, `["queue", "review-inbox", "count"]`. Review/finance mutations also invalidate `["queue"]`.
- Visual tasks (8–9) invoke the `frontend-design` skill first and keep the P3 density tokens. Chips: Pending review = amber outline; Rejected = destructive; Over budget = muted with a lock icon (same tone as `held`).

## Review Focus

1. Reservation arithmetic and the row lock: two concurrent enqueues against a budget that fits one produce exactly one reservation and one hold. Task 2 `test_concurrent_reserve_row_lock`, `test_fits_matrix`.
2. Ledger idempotency: a redelivered archive or a double reject writes no second row, and a release always equals its reservation. Task 2 `test_append_idempotent`, Task 4 `test_archive_redelivery_no_double_charge`.
3. Review state machine and reviewer scope: pending→approved|rejected only (409 otherwise), and reviewers act only on their printers. Task 3 `test_needs_review_matrix`, Task 6 `test_review_transitions_409`, `test_reviewer_limited_to_printers`.
4. Billing off = zero ledger rows and an unchanged queue. Partial, failed and rejected runs settle correctly. Task 3 `test_billing_off_no_rows`, Task 4 `test_failed_partial_charge`, `test_unstarted_failure_keeps_reservation`.
5. Nightly recompute equals the ledger, and the monthly cap rolls over. Task 7 `test_recompute_matches_ledger`, Task 2 `test_monthly_rollover`. All new mutating routes are gated (`test_perm_sweep.py` green, no allowlist additions).

---

### Task 1: Schema: cost_centers, ledger_entries, print_queue review/billing columns, setting

**Files:** Create `backend/app/models/billing.py`, `backend/alembic/versions/d2a7c4e9f1b3_p7_billing.py`, `backend/tests/test_migrations_p7.py`. Modify `backend/app/models/{__init__,enums,library}.py`, `backend/app/services/app_config.py`.

**Interfaces:**
```python
class CostCenter(Base):  # "cost_centers"
    id; name str NOT NULL; owner_user_id FK?; group_id FK?; budget_total Numeric(12,2)?;
    budget_monthly Numeric(12,2)?; balance Numeric(12,2) NOT NULL default 0; active bool default true;
    created_by_user_id FK users SET NULL index; created_at; updated_at
class LedgerEntry(Base):  # "ledger_entries"; index (cost_center_id, created_at)
    id; cost_center_id FK NOT NULL; kind str_enum(LedgerKind,"ledger_kind"); amount Numeric(12,2) NOT NULL;
    print_queue_id FK?; print_id FK?; created_by_user_id FK?; note text?; created_at server now()
PrintQueueEntry (+): cost_center_id FK?; review_state str_enum(ReviewState,"review_state") NOT NULL default 'none' index;
    reviewed_by FK users?; reviewed_at timestamptz?; review_note text?; reserved_cost Numeric(12,2)?; hold_reason text?
AppConfig (+): billing_enabled: bool = False
```
- [ ] Write `test_migrations_p7.py`. Upgrade adds the tables and columns, and an existing P6 queue row reads `review_state='none'` with NULL billing columns. The CHECK rejects both-set and neither-set centers. A second private center for the same owner is rejected. `uq_ledger_queue_kind` rejects a duplicate `(queue, 'charge')` but allows two deposits with NULL queue. Down/up round-trips. Add `test_billing_enabled_defaults_false` (in `tests/test_app_config_api.py`).
- [ ] Run `timeout 600 uv run pytest tests/test_migrations_p7.py tests/test_migration_drift.py tests/test_app_config_api.py -q`. Expect FAIL.
- [ ] Implement the models and migration (server defaults `balance 0`, `active true`, `review_state 'none'`). Run → PASS, then `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`.
- [ ] Commit `feat(db): cost centers, ledger and queue review columns`.

### Task 2: Ledger service (append, figures, reserve/release/charge, row lock)

**Files:** Create `backend/app/services/billing.py`, `backend/tests/billing_fixtures.py` (register in `conftest.pytest_plugins`), `backend/tests/test_billing_ledger.py`.

**Interfaces:**
```python
HOLD_OVER_BUDGET = "over_budget"
BALANCE_KINDS = (LedgerKind.DEPOSIT, LedgerKind.ADJUSTMENT, LedgerKind.CHARGE)
RESERVE_KINDS = (LedgerKind.RESERVATION, LedgerKind.RELEASE)
def q2(x: float | Decimal) -> Decimal
def append(s, cc_id: int, kind: LedgerKind, amount: Decimal, *, queue_id=None, print_id=None,
           user_id=None, note=None) -> LedgerEntry | None   # pg ON CONFLICT DO NOTHING on uq_ledger_queue_kind;
                                                            # BALANCE_KINDS also do `UPDATE cost_centers SET balance = balance + amount`
def lock_center(s, cc_id: int) -> CostCenter                # select(...).with_for_update()
def ensure_private_center(s, user_id: int) -> CostCenter    # INSERT ON CONFLICT DO NOTHING on uq_cost_centers_owner, then select; name = display_name or username
def usable_centers(s, user_id: int) -> list[CostCenter]     # own private + active centers of the user's groups
def reserve(s, cfg, item: PrintQueueEntry, est: Decimal | None, *, user_id) -> bool
def release(s, item: PrintQueueEntry, *, user_id=None) -> LedgerEntry | None   # no-op without a reservation row
def charge(s, item: PrintQueueEntry, run: Print) -> LedgerEntry | None          # amount = -q2(run.total_cost or 0)
def transfer(s, src: PrintQueueEntry, clone: PrintQueueEntry, amount: Decimal) -> None  # clone reservation, no budget check
def retry_over_budget(s, cfg, cc_id: int) -> int            # FIFO by position; returns # unheld
def recompute(s, cc_id: int | None = None) -> dict[int, tuple[Decimal, Decimal]]  # {id: (old, new)}
```
The core arithmetic, which is the only code body in this plan:
```python
@dataclass(frozen=True)
class Figures:
    balance: Decimal; open_reserved: Decimal; month_spent: Decimal; available: Decimal | None

def figures(s: Session, cc: CostCenter, *, now: datetime) -> Figures:
    month_start = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    amt = LedgerEntry.amount
    bal, res, month = s.execute(
        select(
            func.coalesce(func.sum(case((LedgerEntry.kind.in_(BALANCE_KINDS), amt))), 0),
            func.coalesce(func.sum(case((LedgerEntry.kind.in_(RESERVE_KINDS), amt))), 0),
            func.coalesce(func.sum(case((and_(LedgerEntry.kind == LedgerKind.CHARGE,
                                              LedgerEntry.created_at >= month_start), amt))), 0),
        ).where(LedgerEntry.cost_center_id == cc.id)
    ).one()
    open_reserved = -res
    available = None if cc.budget_total is None else cc.budget_total + bal - open_reserved
    return Figures(bal, open_reserved, -month, available)

def fits(cc: CostCenter, f: Figures, est: Decimal) -> bool:
    if f.available is not None and est > f.available:
        return False
    if cc.budget_monthly is not None and f.month_spent + f.open_reserved + est > cc.budget_monthly:
        return False
    return True

def reserve(s, cfg, item, est, *, user_id, now=None) -> bool:
    if not cfg.billing_enabled or item.cost_center_id is None:
        return True
    cc = lock_center(s, item.cost_center_id)          # held until the CALLER commits
    if _reservation(s, item.id) is not None:          # retry/redelivery: already reserved
        return True
    est = q2(est or 0)
    if not fits(cc, figures(s, cc, now=now or datetime.now(UTC)), est):
        item.hold_reason = HOLD_OVER_BUDGET
        return False
    append(s, cc.id, LedgerKind.RESERVATION, -est, queue_id=item.id, user_id=user_id)
    item.reserved_cost, item.hold_reason = est, None
    return True
```
Gates read the ledger under the lock (authoritative). `cost_centers.balance` is a display cache only.
- [ ] Write the tests:
  - `test_fits_matrix`: unlimited; `budget_total` with deposit/charge; monthly cap; both caps.
  - `test_append_idempotent`: a second `(queue, reservation)` returns None and leaves one row.
  - `test_release_negates_reservation`.
  - `test_charge_updates_balance_cache`.
  - `test_monthly_rollover`: a charge `created_at` last month does not count; this month's does.
  - `test_over_budget_sets_hold`.
  - `test_retry_over_budget_after_deposit`.
  - `test_ensure_private_center_race`: two calls give one row.
  - `test_concurrent_reserve_row_lock`: two threads with separate sync Sessions on the testcontainer engine; each reserves a 6.00 item against `budget_total=10`; a `threading.Barrier` sits before `reserve`. Exactly one reservation row and one `over_budget` hold.
- [ ] Run → FAIL. Implement. Run → PASS. Commit `feat(billing): ledger service with row-locked reservations`.

### Task 3: Review state + enqueue hooks + dispatcher filter + send gate

**Files:** Create `backend/app/services/review.py`, `backend/tests/test_queue_review.py`, `backend/tests/test_billing_hooks.py`. Modify:
- `backend/app/services/{queue,dispatcher,print_plan,permissions}.py`
- `backend/app/tasks/slicing.py`
- `backend/app/schemas/queue.py`
- `backend/tests/test_dispatcher.py`: one case

**Interfaces:**
```python
# review.py
def needs_review(ctx: AuthContext, targets: set[int] | None) -> bool
#   admin or queue:review -> False; targets None = every printer in ctx.printer_acl with can_print; any(requires_review)
def item_targets(s, item) -> set[int] | None        # {item.printer_id} | {recipe.target_printer_id} | None
def review_printers(s, item) -> set[int]            # creator's merged ACL (permissions.printer_acl_sync) where can_print & requires_review, ∩ item_targets
def can_review(ctx: AuthContext, s, item) -> bool    # admin, or queue:review and ∃ p ∈ review_printers(item) with ctx.printer_acl[p].can_print
# permissions.py (+)
def printer_acl_sync(s, user_id: int) -> dict[int, PrinterAcl]   # refactor can_print_sync onto it
# print_plan.py (+)
def estimate_from_plate(plate: dict, usages: list[tuple[Spool | None, float]], rates: CostRates) -> float | None
#   extracted from build_plan lines `est_duration_s ... est_cost` (build_plan calls it; no behaviour change)
def estimate_item_sync(s, settings, item) -> float | None   # sliced file's BlobMeta plate + item.ams_mapping trays on
#   item.printer_id (spool_for_tray), global rate when printer unknown; None when no meta
# QueueEnqueueIn (+) cost_center_id: int | None = None
# QueueEntryOut (+) cost_center_id, review_state, reviewed_by, reviewed_at, review_note, reserved_cost: float|None, hold_reason
```
- [ ] Write the tests.
  - `test_queue_review.py`:
    - `test_needs_review_matrix`: target set, recipe target, NULL target as fanout over the ACL, reviewer, admin.
    - Enqueue on a `requires_review` printer → `pending`; on a plain one → `none`.
    - `test_pending_and_rejected_not_eligible`.
    - `test_over_budget_not_eligible`.
    - `test_create_job_queue_required`: `POST /print/send` on a review printer by a member gives 409 `queue_required`; with billing on it gives 409 for everyone.
  - `test_billing_hooks.py`:
    - `test_billing_off_no_rows`: enqueue/dispatch/delete leave zero ledger rows and `cost_center_id/reserved_cost/hold_reason` NULL.
    - `test_enqueue_reserves_private_center`.
    - `test_enqueue_group_center_requires_membership`: 403 for a non-member; 422 for an inactive center.
    - `test_recipe_item_reserves_after_slice`.
    - `test_clone_transfer`: copies=3 under single fanout. The original reserves 3u; the remainder clone gets a 2u reservation (conservative double-count until the original settles).
    - `test_delete_releases`.
    - `test_retry_over_budget_rereserves`.
- [ ] Run → FAIL.
- [ ] Implement:
  - `enqueue_file`: after `db.add(entry); await db.flush()`, run `review_state = PENDING if needs_review(...) else NONE`. If billing is on, set `cost_center_id` = payload id, after a usable-center check, or `ensure_private_center`. For as-sliced items, `reserve(estimate_item_sync)`. All of it runs in one `await db.run_sync(...)` before the existing commit.
  - `slicing.py`: at READY, call `reserve(estimate_item_sync)` when `cost_center_id` is set.
  - `_dispatch`: `unit = item.reserved_cost / original_copies` (when not None), and `transfer(item, clone, unit * clone.copies)` after each `_clone_entry`. Clones inherit `cost_center_id` and `review_state`.
  - `eligible_queue_items`: add `review_state.in_((NONE, APPROVED))` and `hold_reason.is_(None)`.
  - `remove_entry`: release before delete.
  - `retry_entry`: also accepts `hold_reason=over_budget`, which re-runs `reserve`.
  - `create_job`: raise 409 `queue_required` when `needs_review(ctx,{printer_id})` or `billing_enabled`.
- [ ] Run → PASS (plus `tests/test_dispatcher.py tests/test_queue_api.py`). Commit `feat(queue): review state, budget reservation and dispatch gate`.

### Task 4: Archive settlement hook (release then charge)

**Files:** Create `backend/tests/test_archive_billing.py`. Modify `backend/app/services/{archive,billing}.py`.

**Interfaces:** `billing.settle_job(s, job: PrintJob, run: Print) -> None`. It finds `PrintQueueEntry.print_job_id == job.id`. It returns when there is no item, when `job.started_at is None` (the P6 retry path keeps its reservation), or when the item has no reservation and `cost_center_id` is NULL. Otherwise it calls `release(item)` and then `charge(item, run)`, with `print_id=run.id`. `archive_job` calls it after `run.total_cost` is set, before its single commit (`session.flush()` first so `run.id` exists).
- [ ] Tests:
  - `test_completed_release_then_charge`: the order is release then charge, the amounts are exact, and the balance cache moves by −total.
  - `test_failed_partial_charge`: a `FAILED` result at layer 30/100 charges the pro-rata `total_cost` from `charged_grams`.
  - `test_unstarted_failure_keeps_reservation`.
  - `test_archive_redelivery_no_double_charge`: run `archive_job` twice and get one release and one charge.
  - `test_billing_off_mid_flight_still_settles`.
  - `test_no_queue_item_no_charge`.
- [ ] Run → FAIL → implement → PASS (plus `tests/test_archive_service.py`). Commit `feat(billing): charge actual run cost at archive`.

### Task 5: Finance API

**Files:** Create `backend/app/api/finance.py`, `backend/app/schemas/finance.py`, `backend/tests/test_finance_api.py`. Modify `backend/app/api/__init__.py`, `backend/tests/test_auth_sweep.py` (`_PATH_PARAM_VALUES["cost_center_id"]`).

**Interfaces:**
```
GET    /api/finance/config                 session          -> {billing_enabled}
PUT    /api/finance/config                 billing:manage   {billing_enabled}; off -> clear over_budget holds
GET    /api/finance/cost-centers?mine=     session          manage: all; else own private (lazily ensured) + own groups' centers.
                                                            mine=1 restricts to usable (picker). -> CostCenterOut{id,name,owner_user_id,group_id,
                                                            budget_total,budget_monthly,balance,open_reserved,month_spent,available,active}
GET    /api/finance/cost-centers/{id}      session          403 unless visible
POST   /api/finance/cost-centers           billing:manage   exactly one of owner_user_id|group_id (422 otherwise)
PATCH  /api/finance/cost-centers/{id}      billing:manage   name, budgets, active; budget change -> retry_over_budget
DELETE /api/finance/cost-centers/{id}      billing:manage   409 if any ledger row or queue item references it
GET    /api/finance/ledger?cost_center=&limit=50&before_id=  session (visible center only) -> newest first
POST   /api/finance/deposits               billing:manage   {cost_center_id, amount, kind: deposit|adjustment = deposit, note}
                                                            deposit amount>0, adjustment !=0 (422); -> retry_over_budget; created_by = ctx.user
```
Money is serialized as a JSON number with 2 dp (`float(q2)`).
- [ ] Tests: member sees only own and group centers (403 on another's center and ledger); manager CRUD; the exactly-one 422; DELETE 409; deposit validation; a deposit unholds an over-budget item; the config toggle clears holds; viewer 403 on every mutating route (the existing sweep).
- [ ] Run → FAIL → implement → PASS (plus `tests/test_perm_sweep.py tests/test_auth_sweep.py`). Commit `feat(api): finance cost centers, ledger and deposits`.

### Task 6: Review API + inbox

**Files:** Create `backend/tests/test_review_api.py`. Modify `backend/app/api/queue.py`, `backend/app/services/queue.py`, `backend/app/schemas/queue.py`, `backend/app/api/auth.py` (`PrinterFlags` += `review: bool`).

**Interfaces:**
```
GET  /api/queue/review-inbox          queue:review   pending items where can_review(ctx) -> QueueEntryOut[]   (declared before /{entry_id} routes)
GET  /api/queue/review-inbox/count    queue:review   -> {count}
POST /api/queue/{entry_id}/review     queue:review   ReviewIn{action: approve|reject, note?: str<=500}
     404 unknown; 403 unless can_review; 409 unless review_state == pending;
     approve -> approved; reject -> rejected + billing.release; sets reviewed_by/at/note; one commit
```
- [ ] Tests:
  - `test_review_transitions_409`: approve twice gives 409, reject after approve gives 409, and review on `none` gives 409.
  - `test_reject_releases`.
  - `test_approved_becomes_eligible`.
  - `test_reviewer_limited_to_printers`: a reviewer without access to the item's printer gets 403 and does not see it in the inbox. A NULL-target item is visible when the reviewer can print on any of the creator's review printers.
  - `test_inbox_count`.
  - `test_me_printer_access_review_flag`.
- [ ] Run → FAIL → implement → PASS (plus `tests/test_perm_sweep.py`). Commit `feat(api): queue review inbox and approve/reject`.

### Task 7: Nightly balance recompute beat

**Files:** Create `backend/app/tasks/billing.py`, `backend/tests/test_billing_recompute.py`. Modify `backend/app/tasks/celery_app.py` (add `app.tasks.billing` to `imports`; additive `beat_schedule["billing-recompute"] = {"task": "app.tasks.billing.recompute_balances", "schedule": crontab(hour=0, minute=5)}`).

**Interfaces:** `recompute_balances() -> dict`. It opens a sync session, runs `billing.recompute(s)` (it does nothing unless at least one center exists), and then `retry_over_budget` for every center with held items, so the monthly rollover unholds them. It logs a warning for each drifted center, writes `created_by_user_id=NULL`, and commits.
- [ ] Tests: `test_recompute_matches_ledger` (corrupt `balance` directly, recompute, cache == Σ BALANCE_KINDS); drift is reported in the return; `test_rollover_unholds` (an item held on the monthly cap last month is unheld by the run on the 1st, with `now` injected); a beat entry exists and routes to `io`.
- [ ] Run → FAIL → implement → PASS. Commit `feat(billing): nightly balance recompute`.

### Task 8: Web Finance page + nav

**Files:** Create `web/src/api/finance.ts`, `web/src/pages/FinancePage.tsx`, `web/src/pages/FinancePage.test.tsx`, `web/src/components/finance/{CostCenterTable,CostCenterForm,LedgerTable,DepositDialog}.tsx`. Modify `web/src/types.ts`, `web/src/routes.tsx` (`/system/finance`), `web/src/components/shell/navItems.ts` (SYSTEM_ITEMS Finance, icon `Wallet`, `billingGated: true`), `web/src/components/shell/AppSidebar.tsx` (filter: `billingGated` shown iff `can("billing:manage") || billingEnabled`), `web/src/components/shell/AppSidebar.test.tsx`.

**Interfaces:** `useFinanceConfig()`, `useSetBilling()`, `useCostCenters({mine})`, `useCostCenter(id)`, `useLedger(id)`, `useSaveCostCenter()`, `useDeleteCostCenter()`, `useDeposit()`; types `CostCenter`, `LedgerEntry`, `LedgerKind`, `ReviewState`.
- Page: a billing on/off switch (manager only); a centers table (name, owner/group, balance, open reserved, month spent / monthly cap, available, active), where clicking a row opens its ledger (kind chip, signed amount, run/queue links, note, by, at). Managers also get New center, Edit, Deposit/Adjust and Delete (409 → toast). Members see a read-only view of their own centers.
- [ ] Tests: the manager sees controls and the member doesn't; deposit posts `kind`/`amount`; the ledger renders signed amounts; the nav shows Finance for a manager, and for a member only when billing is on.
- [ ] Run → FAIL → implement → PASS + build + oxlint. Commit `feat(web): finance page and nav entry`.

### Task 9: Web review inbox, queue chips, PrintDialog cost-center picker

**Files:** Create `web/src/components/print/ReviewInboxCard.tsx` (+ `.test.tsx`), `web/src/components/print/ReviewActions.tsx`. Modify `web/src/api/queue.ts` (`useReviewInbox`, `useReviewCount` polling 30 s with `enabled: can("queue:review")`, `useReviewEntry`), `web/src/pages/PrintDashboardPage.tsx` (push the card into `railExtras` when the user can review and count > 0), `web/src/components/shell/AppSidebar.tsx` (badge on `/print/queue` = review count with its own aria-label "N awaiting review"), `web/src/pages/QueuePage.tsx` (+ test), `web/src/components/print/PrintDialog.tsx` (+ test), `web/src/types.ts`, `web/src/lib/permissions.ts` (`needsReview(me, printerId|null)`).
- The card lists the pending items (model, plate, printer or "Any", creator, est cost) with Approve, and Reject with an optional note popover.
- QueuePage shows these chips next to `QueueStateChip`: Pending review (`review_state=pending`), Rejected, and Over budget (`hold_reason=over_budget`, with a "Retry budget" button through the existing retry). `ReviewActions` appears on pending rows for reviewers.
- PrintDialog Send step: when billing is on, a cost-center Select (`useCostCenters({mine:true})`, default Personal, available shown) sends `cost_center_id`. When `needsReview` or billing is on, **Send now** becomes **Send next** (`useEnqueue({..., front:true})`) with a hint ("Needs review" / "Billed to <center>"), because the server answers 409 `queue_required`.
- [ ] Tests: the card approve/reject calls the endpoint and invalidates the queue; the three chips render; the picker appears only with billing on and posts `cost_center_id`; Send next replaces Send now on a review printer; the badge count shows.
- [ ] Run → FAIL → implement → PASS + build + oxlint. Commit `feat(web): review inbox, budget chips and cost-center picker`.

### Task 10: Docs + live-verify checklist

**Files:** Create `docs/superpowers/plans/2026-10-22-p7-live-verify.md`. Modify `README.md` (billing toggle, cost-center model: allowance/monthly cap, signs, review flow, nightly recompute).

- Live-verify checklist:
  - [ ] a member on a `requires_review` printer → Pending; the reviewer approves → it prints on the A1 Mini
  - [ ] reject → Rejected, and the reservation is released in the ledger
  - [ ] `budget_total=1.00` and a larger print → Over budget; a deposit → it unholds and dispatches
  - [ ] after the run the ledger shows reservation → release → charge = `prints.total_cost`
  - [ ] a run cancelled at ~20% → a partial charge
  - [ ] billing off → the dialog shows Send now and no ledger rows appear
  - [ ] the next morning, the `billing-recompute` log shows zero drift
- [ ] The controller runs the spec Verification block. Commit `docs(billing): P7 live-verify checklist and setup`.

---

## Self-review + pre-flight conflict scan (resolved inline)

- **Spec coverage:** the §P7 schema, flow, API/UI and test bullets map to Tasks 1–9; the drill maps to Task 10. Integration rule: `created_by_user_id` is on centers and ledger rows (NULL from beat); queueing stays `printers:print`.
- **One filter:** only `eligible_queue_items` changes in the dispatcher's selection, so P8's `printer_hold_reason` is untouched. The `_dispatch` clone transfer is a ledger write, not a filter.
- **Bypass via direct send:** `create_job` is the shared async seam for `/print/send` and printer-page sends. The 409 closes the review and budget bypass. The sync `create_job_sync` (dispatcher) is not gated.
- **Async vs sync:** one sync `billing` implementation used via `run_sync`. It never commits, so the lock spans check+insert until the caller's commit.
- **`prints.total_cost` is Float:** `q2(Decimal(str(x)))` at the ledger boundary; schema unchanged.
- **Route order:** `/queue/review-inbox` is declared before `/{entry_id}`. The perm sweep needs no allowlist entries, because every mutating route is gated `billing:manage` or `queue:review`.
- **`compose.yaml` hunk:** no task touches compose.
