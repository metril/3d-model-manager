/**
 * Cost-center table (P7 review/billing plan Task 8): name, owner/group,
 * balance, open reserved, month spent / monthly cap, available, active.
 * Clicking a row selects it (the page shows its ledger below). Managers
 * get New/Edit/Deposit/Delete; members get a read-only view of their own
 * centers only (the `GET /finance/cost-centers?mine=true` the page
 * already scoped the query to).
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useDeleteCostCenter } from "@/api/finance";
import type { CostCenter } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { formatCost } from "@/lib/format";
import { CostCenterForm } from "@/components/finance/CostCenterForm";
import { DepositDialog } from "@/components/finance/DepositDialog";

function CostCenterDialog({
  title,
  center,
  open,
  onOpenChange,
}: {
  title: string;
  center: CostCenter | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [state, setState] = useState({ dirty: false, busy: false });
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} dirty={state.dirty} busy={state.busy}>
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
      </DialogHeader>
      {(center === null || open) && (
        <CostCenterForm center={center} onSaved={() => onOpenChange(false)} onStateChange={setState} />
      )}
    </FormDialog>
  );
}

export function CostCenterTable({
  centers,
  isManager,
  selectedId,
  onSelect,
}: {
  centers: CostCenter[];
  isManager: boolean;
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<CostCenter | null>(null);
  const [depositing, setDepositing] = useState<CostCenter | null>(null);
  const [deleting, setDeleting] = useState<CostCenter | null>(null);
  const del = useDeleteCostCenter();
  const { t } = useTranslation();

  function handleDelete(center: CostCenter) {
    del.mutate(center.id, {
      onError: (e) => {
        toast.error(e instanceof ApiError ? e.detail : t("finance.deleteError"));
      },
    });
  }

  return (
    <div className="space-y-3">
      {isManager && (
        <div className="flex justify-end">
          <Button type="button" size="sm" onClick={() => setCreating(true)}>
            {t("finance.newCenter")}
          </Button>
        </div>
      )}
      {centers.length === 0 ? (
        <p className="py-4 text-center text-sm text-muted-foreground">{t("finance.empty")}</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("finance.col.name")}</TableHead>
              <TableHead>{t("finance.col.owner")}</TableHead>
              <TableHead>{t("finance.col.balance")}</TableHead>
              <TableHead>{t("finance.col.reserved")}</TableHead>
              <TableHead>{t("finance.col.month")}</TableHead>
              <TableHead>{t("finance.col.available")}</TableHead>
              <TableHead>{t("finance.col.active")}</TableHead>
              {isManager && <TableHead className="text-right">{t("finance.col.actions")}</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {centers.map((c) => (
              <TableRow
                key={c.id}
                role="button"
                tabIndex={0}
                aria-selected={c.id === selectedId}
                onClick={() => onSelect(c.id)}
                onKeyDown={(e) => {
                  // Keys from the action buttons inside the row must not
                  // select it (or scroll the page on Space).
                  if (e.target !== e.currentTarget) return;
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(c.id);
                  }
                }}
                className={cn("cursor-pointer", c.id === selectedId && "bg-accent/40")}
              >
                <TableCell className="font-medium">{c.name}</TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {c.owner_user_id != null
                    ? t("finance.userN", { id: c.owner_user_id })
                    : c.group_id != null
                      ? t("finance.groupN", { id: c.group_id })
                      : "—"}
                </TableCell>
                <TableCell className="font-mono tabular-nums">{formatCost(c.balance)}</TableCell>
                <TableCell className="font-mono tabular-nums">{formatCost(c.open_reserved)}</TableCell>
                <TableCell className="font-mono tabular-nums">
                  {formatCost(c.month_spent)}
                  {c.budget_monthly != null ? ` / ${formatCost(c.budget_monthly)}` : ""}
                  {c.budget_monthly != null && c.budget_monthly > 0 && (
                    <Progress
                      className="mt-1 h-1.5 w-24"
                      aria-label={t("finance.budgetUsage")}
                      value={Math.min(100, Math.max(0, (c.month_spent / c.budget_monthly) * 100))}
                    />
                  )}
                </TableCell>
                <TableCell className="font-mono tabular-nums">
                  {c.available != null ? formatCost(c.available) : "—"}
                </TableCell>
                <TableCell>
                  <Badge variant={c.active ? "outline" : "destructive"}>{c.active ? t("finance.col.active") : t("finance.inactive")}</Badge>
                </TableCell>
                {isManager && (
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                      <Button type="button" size="sm" variant="outline" onClick={() => setEditing(c)}>
                        {t("finance.edit")}
                      </Button>
                      <Button type="button" size="sm" variant="outline" onClick={() => setDepositing(c)}>
                        {t("finance.deposit")}
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="destructive"
                        disabled={del.isPending}
                        onClick={() => setDeleting(c)}
                      >
                        {t("finance.delete")}
                      </Button>
                    </div>
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <CostCenterDialog title={t("finance.newTitle")} center={null} open={creating} onOpenChange={setCreating} />

      <CostCenterDialog
        title={t("finance.editTitle", { name: editing?.name ?? "" })}
        center={editing}
        open={editing != null}
        onOpenChange={(open) => !open && setEditing(null)}
      />

      <ConfirmDialog
        open={deleting != null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={t("finance.deleteTitle", { name: deleting?.name ?? "" })}
        description={t("finance.deleteDesc")}
        confirmLabel={t("finance.delete")}
        cancelLabel={t("common.cancel")}
        destructive
        onConfirm={() => deleting && handleDelete(deleting)}
      />

      <DepositDialog
        center={depositing}
        open={depositing != null}
        onOpenChange={(open) => !open && setDepositing(null)}
      />
    </div>
  );
}
