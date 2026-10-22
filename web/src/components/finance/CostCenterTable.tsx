/**
 * Cost-center table (P7 review/billing plan Task 8): name, owner/group,
 * balance, open reserved, month spent / monthly cap, available, active.
 * Clicking a row selects it (the page shows its ledger below). Managers
 * get New/Edit/Deposit/Delete; members get a read-only view of their own
 * centers only (the `GET /finance/cost-centers?mine=true` the page
 * already scoped the query to).
 */
import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useDeleteCostCenter } from "@/api/finance";
import type { CostCenter } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { formatCost } from "@/lib/format";
import { CostCenterForm } from "@/components/finance/CostCenterForm";
import { DepositDialog } from "@/components/finance/DepositDialog";

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
  const del = useDeleteCostCenter();

  function handleDelete(center: CostCenter) {
    del.mutate(center.id, {
      onError: (e) => {
        toast.error(e instanceof ApiError ? e.detail : "Could not delete the cost center.");
      },
    });
  }

  return (
    <div className="space-y-3">
      {isManager && (
        <div className="flex justify-end">
          <Button type="button" size="sm" onClick={() => setCreating(true)}>
            New center
          </Button>
        </div>
      )}
      {centers.length === 0 ? (
        <p className="py-4 text-center text-sm text-muted-foreground">No cost centers yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Owner / group</TableHead>
              <TableHead>Balance</TableHead>
              <TableHead>Open reserved</TableHead>
              <TableHead>Month spent / cap</TableHead>
              <TableHead>Available</TableHead>
              <TableHead>Active</TableHead>
              {isManager && <TableHead className="text-right">Actions</TableHead>}
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
                  if (e.key === "Enter" || e.key === " ") onSelect(c.id);
                }}
                className={cn("cursor-pointer", c.id === selectedId && "bg-accent/40")}
              >
                <TableCell className="font-medium">{c.name}</TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {c.owner_user_id != null ? `User #${c.owner_user_id}` : c.group_id != null ? `Group #${c.group_id}` : "—"}
                </TableCell>
                <TableCell className="font-mono tabular-nums">{formatCost(c.balance)}</TableCell>
                <TableCell className="font-mono tabular-nums">{formatCost(c.open_reserved)}</TableCell>
                <TableCell className="font-mono tabular-nums">
                  {formatCost(c.month_spent)}
                  {c.budget_monthly != null ? ` / ${formatCost(c.budget_monthly)}` : ""}
                </TableCell>
                <TableCell className="font-mono tabular-nums">
                  {c.available != null ? formatCost(c.available) : "—"}
                </TableCell>
                <TableCell>
                  <Badge variant={c.active ? "outline" : "destructive"}>{c.active ? "Active" : "Inactive"}</Badge>
                </TableCell>
                {isManager && (
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                      <Button type="button" size="sm" variant="outline" onClick={() => setEditing(c)}>
                        Edit
                      </Button>
                      <Button type="button" size="sm" variant="outline" onClick={() => setDepositing(c)}>
                        Deposit
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="destructive"
                        disabled={del.isPending}
                        onClick={() => handleDelete(c)}
                      >
                        Delete
                      </Button>
                    </div>
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New cost center</DialogTitle>
          </DialogHeader>
          <CostCenterForm center={null} onSaved={() => setCreating(false)} />
        </DialogContent>
      </Dialog>

      <Dialog open={editing != null} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit {editing?.name}</DialogTitle>
          </DialogHeader>
          {editing && <CostCenterForm center={editing} onSaved={() => setEditing(null)} />}
        </DialogContent>
      </Dialog>

      <DepositDialog center={depositing} open={depositing != null} onOpenChange={(open) => !open && setDepositing(null)} />
    </div>
  );
}
