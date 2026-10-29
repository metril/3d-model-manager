import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useSkipObjects } from "@/api/printerControl";
import type { PrintableObject } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";

/** Pick printable objects of the active print to skip (objects come from the archived 3MF). */
export function SkipObjectsDialog({
  printerId,
  objects,
  open,
  onOpenChange,
}: {
  printerId: number;
  objects: PrintableObject[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const skip = useSkipObjects(printerId);
  const toggle = (id: number) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  // Leaving every object skipped is just a stop; keep at least one.
  const canSubmit = picked.size > 0 && picked.size < objects.length && !skip.isPending;

  return (
    <FormDialog open={open} onOpenChange={onOpenChange} dirty={picked.size > 0} busy={skip.isPending}>
        <DialogHeader>
          <DialogTitle>{t("card.skip.title")}</DialogTitle>
          <DialogDescription>{t("card.skip.desc")}</DialogDescription>
        </DialogHeader>
        <ul className="flex max-h-64 flex-col gap-1 overflow-y-auto">
          {objects.map((o) => (
            <li key={o.id}>
              <label className="flex items-center gap-2 rounded-md px-1 py-1 text-sm hover:bg-muted">
                <Checkbox checked={picked.has(o.id)} onCheckedChange={() => toggle(o.id)} aria-label={o.name} />
                <span className="truncate">{o.name}</span>
                <span className="ml-auto font-mono text-xs text-muted-foreground">#{o.id}</span>
              </label>
            </li>
          ))}
        </ul>
        <DialogFooter showCloseButton>
          <Button
            type="button"
            variant="destructive"
            disabled={!canSubmit}
            onClick={() =>
              skip.mutate([...picked], {
                onSuccess: () => {
                  setPicked(new Set());
                  onOpenChange(false);
                },
              })
            }
          >
            {t("card.skip.submit", { n: picked.size || "" })}
          </Button>
        </DialogFooter>
    </FormDialog>
  );
}
