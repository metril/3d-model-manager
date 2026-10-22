/** Deposit/adjustment dialog (P7 review/billing plan Task 8), manager only. */
import { useState } from "react";

import { ApiError } from "@/api/client";
import { useDeposit } from "@/api/finance";
import type { CostCenter, LedgerKind } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";

function errText(e: unknown, fallback: string) {
  return e instanceof ApiError ? e.detail : fallback;
}

export function DepositDialog({
  center,
  open,
  onOpenChange,
}: {
  center: CostCenter | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [kind, setKind] = useState<LedgerKind>("deposit");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const deposit = useDeposit();

  function reset() {
    setKind("deposit");
    setAmount("");
    setNote("");
    deposit.reset();
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!center) return;
    deposit.mutate(
      { cost_center_id: center.id, amount: Number(amount), kind, note: note.trim() || null },
      {
        onSuccess: () => {
          onOpenChange(false);
          reset();
        },
      },
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Deposit / adjust{center ? ` — ${center.name}` : ""}</DialogTitle>
        </DialogHeader>
        <form className="space-y-3" onSubmit={handleSubmit}>
          <RadioGroup value={kind} onValueChange={(v) => setKind(v as LedgerKind)}>
            <div className="flex items-center gap-2">
              <RadioGroupItem value="deposit" id="deposit-kind-deposit" />
              <Label htmlFor="deposit-kind-deposit" className="font-normal">
                Deposit (adds funds)
              </Label>
            </div>
            <div className="flex items-center gap-2">
              <RadioGroupItem value="adjustment" id="deposit-kind-adjustment" />
              <Label htmlFor="deposit-kind-adjustment" className="font-normal">
                Adjustment (can be negative)
              </Label>
            </div>
          </RadioGroup>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="deposit-amount">Amount</Label>
            <Input
              id="deposit-amount"
              type="number"
              step="0.01"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              required
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="deposit-note">Note</Label>
            <Input id="deposit-note" value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          {deposit.isError && (
            <p role="alert" className="text-sm text-destructive">
              {errText(deposit.error, "Could not post the entry.")}
            </p>
          )}
          <Button type="submit" disabled={deposit.isPending || !amount.trim()}>
            Post
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
