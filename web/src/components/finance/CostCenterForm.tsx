/**
 * New/edit cost-center form (P7 review/billing plan Task 8). Owner vs
 * group isn't editable once created (`CostCenterPatchIn` has no such
 * field) -- only shown for a new center.
 */
import { useState } from "react";

import { useSaveCostCenter } from "@/api/finance";
import { ApiError } from "@/api/client";
import type { CostCenter } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

function errText(e: unknown, fallback: string) {
  return e instanceof ApiError ? e.detail : fallback;
}

export function CostCenterForm({
  center,
  onSaved,
}: {
  /** `null` creates a new (owner-less) private-style center; pass an
   * explicit `owner_user_id`/`group_id` center creation flow isn't needed
   * here -- managers create group centers from the Groups admin UI
   * elsewhere, so this form only covers name/budgets. */
  center: CostCenter | null;
  onSaved?: () => void;
}) {
  const [name, setName] = useState(center?.name ?? "");
  const [ownerUserId, setOwnerUserId] = useState("");
  const [groupId, setGroupId] = useState("");
  const [budgetTotal, setBudgetTotal] = useState(center?.budget_total != null ? String(center.budget_total) : "");
  const [budgetMonthly, setBudgetMonthly] = useState(
    center?.budget_monthly != null ? String(center.budget_monthly) : "",
  );
  const save = useSaveCostCenter();

  function parseBudget(v: string): number | null {
    const trimmed = v.trim();
    return trimmed === "" ? null : Number(trimmed);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    save.mutate(
      {
        id: center?.id,
        body: center
          ? {
              name: name.trim(),
              budget_total: parseBudget(budgetTotal),
              budget_monthly: parseBudget(budgetMonthly),
            }
          : {
              name: name.trim(),
              owner_user_id: ownerUserId.trim() ? Number(ownerUserId) : null,
              group_id: groupId.trim() ? Number(groupId) : null,
              budget_total: parseBudget(budgetTotal),
              budget_monthly: parseBudget(budgetMonthly),
            },
      },
      { onSuccess: () => onSaved?.() },
    );
  }

  return (
    <form className="space-y-3" onSubmit={handleSubmit}>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="cost-center-name">Name</Label>
        <Input id="cost-center-name" value={name} onChange={(e) => setName(e.target.value)} required />
      </div>
      {!center && (
        <div className="flex gap-3">
          <div className="flex flex-1 flex-col gap-1.5">
            <Label htmlFor="cost-center-owner">Owner user ID</Label>
            <Input
              id="cost-center-owner"
              type="number"
              value={ownerUserId}
              onChange={(e) => {
                setOwnerUserId(e.target.value);
                if (e.target.value.trim()) setGroupId("");
              }}
              placeholder="Exactly one of owner/group"
            />
          </div>
          <div className="flex flex-1 flex-col gap-1.5">
            <Label htmlFor="cost-center-group">Group ID</Label>
            <Input
              id="cost-center-group"
              type="number"
              value={groupId}
              onChange={(e) => {
                setGroupId(e.target.value);
                if (e.target.value.trim()) setOwnerUserId("");
              }}
            />
          </div>
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="cost-center-budget-total">Total budget</Label>
        <Input
          id="cost-center-budget-total"
          type="number"
          min={0}
          step="0.01"
          value={budgetTotal}
          onChange={(e) => setBudgetTotal(e.target.value)}
          placeholder="No limit"
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="cost-center-budget-monthly">Monthly budget</Label>
        <Input
          id="cost-center-budget-monthly"
          type="number"
          min={0}
          step="0.01"
          value={budgetMonthly}
          onChange={(e) => setBudgetMonthly(e.target.value)}
          placeholder="No limit"
        />
      </div>
      {save.isError && (
        <p role="alert" className="text-sm text-destructive">
          {errText(save.error, "Could not save the cost center.")}
        </p>
      )}
      <Button
        type="submit"
        disabled={save.isPending || !name.trim() || (!center && !ownerUserId.trim() && !groupId.trim())}
      >
        {center ? "Save" : "Create center"}
      </Button>
    </form>
  );
}
