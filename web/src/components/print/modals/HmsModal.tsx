import { ExternalLinkIcon } from "lucide-react";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useHms, useHmsAction, useHmsClear } from "@/api/printerOps";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const SEVERITY_CLASS: Record<string, string> = {
  fatal: "text-destructive",
  serious: "text-destructive",
  common: "text-amber-600 dark:text-amber-400",
  info: "text-muted-foreground",
};

function label(action: string) {
  const s = action.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function HmsModal({
  printerId,
  open,
  onOpenChange,
}: {
  printerId: number;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const { canPrinter } = useAuth();
  const canControl = canPrinter(printerId, "control");
  const hms = useHms(printerId, open);
  const clear = useHmsClear(printerId);
  const act = useHmsAction(printerId);
  const items = hms.data ?? [];
  const err = clear.error ?? act.error;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>HMS errors</DialogTitle>
        </DialogHeader>
        {hms.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading...</p>
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No active errors.</p>
        ) : (
          <ul className="max-h-96 space-y-3 overflow-y-auto">
            {items.map((f) => (
              <li key={`${f.kind}-${f.full_code}`} className="space-y-1.5 rounded-md border p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className={`text-xs font-semibold uppercase ${SEVERITY_CLASS[f.severity] ?? ""}`}>
                    {f.severity}
                  </span>
                  <span className="font-mono text-xs text-muted-foreground">{f.code}</span>
                </div>
                <p className="text-sm">{f.description ?? "Unknown code"}</p>
                <p className="text-xs text-muted-foreground">{f.module}</p>
                <div className="flex flex-wrap items-center gap-1.5">
                  {(canControl ? f.actions : []).map((a) => (
                    <Button
                      key={a}
                      size="xs"
                      variant="outline"
                      disabled={act.isPending}
                      onClick={() => act.mutate({ print_error: f.full_code, action: a })}
                    >
                      {label(a)}
                    </Button>
                  ))}
                  <a
                    href={f.wiki_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ml-auto inline-flex items-center gap-1 text-xs text-primary hover:underline"
                  >
                    Wiki <ExternalLinkIcon className="size-3" />
                  </a>
                </div>
              </li>
            ))}
          </ul>
        )}
        {err ? (
          <p role="alert" className="text-sm text-destructive">
            {err instanceof ApiError ? err.detail : "Request failed"}
          </p>
        ) : null}
        {canControl ? (
          <div className="flex justify-end">
            <Button
              variant="outline"
              disabled={clear.isPending || items.length === 0}
              onClick={() => clear.mutate()}
            >
              Clear errors
            </Button>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
