/**
 * Card header: model image (printerImage), name link, nozzle size, lifetime
 * print hours and the overflow menu (Edit / Info / Reconnect / Force refresh /
 * Diagnose / Delete). Info and Diagnose are slot callbacks (Q5-web modals);
 * the menu hides an entry whose callback is absent.
 */
import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { MoreVerticalIcon } from "lucide-react";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { api } from "@/api/client";
import { useAuth } from "@/api/auth";
import { useDeletePrinter } from "@/api/printers";
import type { PrinterOut, PrinterStatusOut, ProbeOut } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { modelKey, printerImage } from "@/lib/printerState";

export function nozzleLabel(printer: PrinterOut, status: PrinterStatusOut): string | null {
  const dia = printer.options?.nozzle_diameter;
  const size = typeof dia === "number" || typeof dia === "string" ? `${dia} mm` : null;
  const type = status.nozzle_type ? status.nozzle_type.replaceAll("_", " ") : null;
  return [size, type].filter(Boolean).join(" ") || null;
}

export function CardHeader({
  printer,
  status,
  onInfo,
  onDiagnose,
  extra,
  imageClassName = "size-14",
}: {
  printer: PrinterOut;
  status: PrinterStatusOut;
  onInfo?: () => void;
  onDiagnose?: () => void;
  /** `header-extra` slot (Q7 maintenance badge / plug indicator land here). */
  extra?: ReactNode;
  imageClassName?: string;
}) {
  const auth = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const del = useDeletePrinter();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const canAdmin = auth.can("settings:write");
  const nozzle = nozzleLabel(printer, status);
  const hours = printer.total_print_hours;

  async function reconnect() {
    try {
      const res = await api.post<ProbeOut>(`/printers/${printer.id}/test`);
      if (res.ok) toast.success("Printer reachable");
      else toast.error(res.detail);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Reconnect failed");
    }
    void qc.invalidateQueries({ queryKey: ["printers"] });
  }

  return (
    <div className="flex items-start gap-3" data-slot="card-header">
      <img
        src={printerImage(modelKey(printer.model, printer.serial))}
        alt=""
        className={`${imageClassName} shrink-0 object-contain`}
      />
      <div className="min-w-0 flex-1">
        <Link
          to="/printers/$id"
          params={{ id: String(printer.id) }}
          className="pc-t14 block truncate font-medium hover:underline"
        >
          {printer.name}
        </Link>
        <p className="pc-t11 truncate text-muted-foreground">
          {[printer.model, nozzle, hours != null ? `${Math.round(hours)} h printed` : null]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </div>
      {extra}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon-sm" aria-label="Printer menu">
            <MoreVerticalIcon />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {canAdmin ? <DropdownMenuItem onSelect={() => void navigate({ to: "/settings" })}>Edit</DropdownMenuItem> : null}
          {onInfo ? <DropdownMenuItem onSelect={onInfo}>Info</DropdownMenuItem> : null}
          <DropdownMenuItem onSelect={() => void reconnect()}>Reconnect</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => void qc.invalidateQueries({ queryKey: ["printers"] })}>
            Force refresh
          </DropdownMenuItem>
          {onDiagnose && canAdmin ? <DropdownMenuItem onSelect={onDiagnose}>Diagnose</DropdownMenuItem> : null}
          {canAdmin ? (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onSelect={() => setConfirmDelete(true)}>
                Delete
              </DropdownMenuItem>
            </>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`Delete ${printer.name}?`}
        description="The printer and its connection settings are removed. Print history is kept."
        confirmLabel="Delete"
        destructive
        onConfirm={() => del.mutate(printer.id)}
      />
    </div>
  );
}
