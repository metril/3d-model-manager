import { useEffect, useRef } from "react";
import { CheckCircle2Icon, MinusCircleIcon, XCircleIcon } from "lucide-react";

import { ApiError } from "@/api/client";
import { useDiagnostic } from "@/api/printerOps";
import type { DiagnosticCheck, DiagnosticIn } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

export function DiagnosticChecks({ checks }: { checks: DiagnosticCheck[] }) {
  return (
    <ul className="space-y-1.5 text-sm">
      {checks.map((c) => (
        <li key={c.id} className="flex items-start gap-2" data-status={c.status}>
          {c.status === "ok" ? (
            <CheckCircle2Icon className="mt-0.5 size-4 shrink-0 text-emerald-600" />
          ) : c.status === "fail" ? (
            <XCircleIcon className="mt-0.5 size-4 shrink-0 text-destructive" />
          ) : (
            <MinusCircleIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          )}
          <span>
            <span className="font-medium">{c.id.replace(/_/g, " ")}</span>
            <span className="block text-xs text-muted-foreground">{c.hint}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Pass `printerId` for a saved printer, or `adhoc` creds from the setup form. */
export function DiagnosticModal({
  printerId,
  adhoc,
  open,
  onOpenChange,
}: {
  printerId?: number;
  adhoc?: DiagnosticIn;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const diag = useDiagnostic();
  const { mutate } = diag;
  const adhocRef = useRef(adhoc);
  adhocRef.current = adhoc;

  function run() {
    if (printerId != null) mutate({ printerId });
    else if (adhocRef.current) mutate({ adhoc: adhocRef.current });
  }
  useEffect(() => {
    if (open) run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, printerId]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Connection diagnostic</DialogTitle>
        </DialogHeader>
        {diag.isPending ? <p className="text-sm text-muted-foreground">Running checks...</p> : null}
        {diag.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {diag.error instanceof ApiError ? diag.error.detail : "Diagnostic failed"}
          </p>
        ) : null}
        {diag.data ? <DiagnosticChecks checks={diag.data.checks} /> : null}
        <div className="flex justify-end">
          <Button variant="outline" disabled={diag.isPending} onClick={run}>
            Run again
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
