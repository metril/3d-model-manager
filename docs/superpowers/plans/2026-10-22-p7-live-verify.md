# P7 live-verify (review gate + billing/ledger -- A1 Mini)

Everything below needs a real cost center/ledger setup and, for the print
steps, a real A1 Mini; none of it is exercised by the automated suite.

- [ ] A member on a `requires_review` printer sends/queues a print → the
      item lands `Pending` (not dispatched); a reviewer with
      `queue:review` over that printer sees it in **Review inbox** and
      **Approve**s it → it prints on the A1 Mini.
- [ ] The same inbox item, **Reject**ed instead → `Rejected`, and any
      reservation the item had posted is released in the ledger (no
      lingering `reservation` row with no matching `release`).
- [ ] A cost center with `budget_total=1.00` and a print whose recipe
      estimate exceeds it → the item holds `over_budget` instead of
      dispatching; a manager posts a **deposit** covering the gap → the
      item unholds and dispatches on the next `dispatch_queue` tick with
      no manual retry.
- [ ] After that run completes, the ledger for the item reads
      `reservation` → `release` → `charge`, and the `charge` amount
      equals the print's `prints.total_cost`.
- [ ] A run cancelled at ~20% progress → the ledger still posts a
      `charge`, for a partial amount (not the full estimate, not zero).
- [ ] With billing OFF, the send/queue dialog shows a plain **Send now**
      (no budget/cost-center UI) and no ledger rows appear for that
      print, regardless of printer ACL.
- [ ] Billing toggled OFF partway through an in-flight print (one that
      reserved against a cost center while billing was still on) → the
      run still settles normally on completion (its `charge` posts; it
      isn't orphaned by the toggle).
- [ ] The next morning, the `billing-recompute` beat log
      (`recompute_balances`) reports zero drift for every cost center
      touched above (`drifted` empty) and `unheld` reflects any
      monthly-cap item that rolled over.

- [ ] The controller runs the spec Verification block.
