/**
 * New/edit cost-center form (P7 review/billing plan Task 8). Owner vs
 * group isn't editable once created (`CostCenterPatchIn` has no such
 * field) -- only shown for a new center.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

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
  onStateChange,
}: {
  /** `null` creates a new (owner-less) private-style center; pass an
   * explicit `owner_user_id`/`group_id` center creation flow isn't needed
   * here -- managers create group centers from the Groups admin UI
   * elsewhere, so this form only covers name/budgets. */
  center: CostCenter | null;
  onSaved?: () => void;
  /** Reports unsaved edits / save-in-flight so a wrapping FormDialog can guard closing. */
  onStateChange?: (state: { dirty: boolean; busy: boolean }) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(center?.name ?? "");
  const [ownerUserId, setOwnerUserId] = useState("");
  const [groupId, setGroupId] = useState("");
  const [budgetTotal, setBudgetTotal] = useState(center?.budget_total != null ? String(center.budget_total) : "");
  const [budgetMonthly, setBudgetMonthly] = useState(
    center?.budget_monthly != null ? String(center.budget_monthly) : "",
  );
  const save = useSaveCostCenter();
  const [baseline] = useState(() => JSON.stringify([name, ownerUserId, groupId, budgetTotal, budgetMonthly]));
  const dirty = JSON.stringify([name, ownerUserId, groupId, budgetTotal, budgetMonthly]) !== baseline;
  const busy = save.isPending;
  useEffect(() => {
    onStateChange?.({ dirty, busy });
  }, [dirty, busy, onStateChange]);
  useEffect(() => () => onStateChange?.({ dirty: false, busy: false }), [onStateChange]);

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
        <Label htmlFor="cost-center-name">{t("finance.col.name")}</Label>
        <Input id="cost-center-name" value={name} onChange={(e) => setName(e.target.value)} required />
      </div>
      {!center && (
        <div className="flex gap-3">
          <div className="flex flex-1 flex-col gap-1.5">
            <Label htmlFor="cost-center-owner">{t("finance.ownerId")}</Label>
            <Input
              id="cost-center-owner"
              type="number"
              value={ownerUserId}
              onChange={(e) => {
                setOwnerUserId(e.target.value);
                if (e.target.value.trim()) setGroupId("");
              }}
              placeholder={t("finance.ownerOrGroup")}
            />
          </div>
          <div className="flex flex-1 flex-col gap-1.5">
            <Label htmlFor="cost-center-group">{t("finance.groupId")}</Label>
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
        <Label htmlFor="cost-center-budget-total">{t("finance.budgetTotal")}</Label>
        <Input
          id="cost-center-budget-total"
          type="number"
          min={0}
          step="0.01"
          value={budgetTotal}
          onChange={(e) => setBudgetTotal(e.target.value)}
          placeholder={t("finance.noLimit")}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="cost-center-budget-monthly">{t("finance.budgetMonthly")}</Label>
        <Input
          id="cost-center-budget-monthly"
          type="number"
          min={0}
          step="0.01"
          value={budgetMonthly}
          onChange={(e) => setBudgetMonthly(e.target.value)}
          placeholder={t("finance.noLimit")}
        />
      </div>
      {save.isError && (
        <p role="alert" className="text-sm text-destructive">
          {errText(save.error, t("finance.saveError"))}
        </p>
      )}
      <Button
        type="submit"
        disabled={save.isPending || !name.trim() || (!center && !ownerUserId.trim() && !groupId.trim())}
      >
        {center ? t("finance.save") : t("finance.create")}
      </Button>
    </form>
  );
}
