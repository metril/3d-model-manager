/** Deposit/adjustment dialog (P7 review/billing plan Task 8), manager only. */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import { useDeposit } from "@/api/finance";
import type { CostCenter, LedgerKind } from "@/api/types";
import { Button } from "@/components/ui/button";
import { DialogClose, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
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
  const { t } = useTranslation();
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
      {
        cost_center_id: center.id,
        amount: Number(amount),
        kind,
        note: note.trim() || null,
      },
      {
        onSuccess: () => {
          onOpenChange(false);
          reset();
        },
      },
    );
  }

  return (
    <FormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
      dirty={kind !== "deposit" || amount !== "" || note !== ""}
      busy={deposit.isPending}
    >
      <DialogHeader>
        <DialogTitle>{t("finance.depositTitle")}{center ? ` — ${center.name}` : ""}</DialogTitle>
      </DialogHeader>
      <form className="space-y-3" onSubmit={handleSubmit}>
        <RadioGroup value={kind} onValueChange={(v) => setKind(v as LedgerKind)}>
          <div className="flex items-center gap-2">
            <RadioGroupItem value="deposit" id="deposit-kind-deposit" />
            <Label htmlFor="deposit-kind-deposit" className="font-normal">
              {t("finance.depositKind")}
            </Label>
          </div>
          <div className="flex items-center gap-2">
            <RadioGroupItem value="adjustment" id="deposit-kind-adjustment" />
            <Label htmlFor="deposit-kind-adjustment" className="font-normal">
              {t("finance.adjustKind")}
            </Label>
          </div>
        </RadioGroup>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="deposit-amount">{t("finance.col.amount")}</Label>
          <Input
            id="deposit-amount"
            type="number"
            step="0.01"
            // Deposits add funds, so zero/negative is nonsense; only
            // adjustments may be negative.
            min={kind === "deposit" ? 0.01 : undefined}
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            required
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="deposit-note">{t("finance.col.note")}</Label>
          <Input id="deposit-note" value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        {deposit.isError && (
          <p role="alert" className="text-sm text-destructive">
            {errText(deposit.error, t("finance.postError"))}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <DialogClose asChild>
            <Button type="button" variant="outline">
              {t("common.cancel")}
            </Button>
          </DialogClose>
          <Button type="submit" disabled={deposit.isPending || !amount.trim() || (kind === "deposit" && !(Number(amount) >= 0.01))}>
            {t("finance.post")}
          </Button>
        </div>
      </form>
    </FormDialog>
  );
}
