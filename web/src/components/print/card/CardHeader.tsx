/**
 * Card header: model image (printerImage), name link, nozzle size, lifetime
 * print hours, the single state pill and the printer menu (Edit / Reconnect /
 * Force refresh / Delete). Info and Diagnose live in the toolbar's Insight group.
 */
import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { MoreVerticalIcon } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
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
import { modelKey, printerImage, toneForGcode } from "@/lib/printerState";
import { cn } from "@/lib/utils";

import { StatePill } from "./StatePill";

import { nozzleLabel } from "./nozzleLabel";

export function CardHeader({
  printer,
  status,
  extra,
  imageClassName = "size-10",
}: {
  printer: PrinterOut;
  status: PrinterStatusOut;
  /** `header-extra` slot (Q7 maintenance badge / plug indicator land here). */
  extra?: ReactNode;
  imageClassName?: string;
}) {
  const { t } = useTranslation();
  const auth = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const del = useDeletePrinter();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const canAdmin = auth.can("settings:write");
  const nozzle = nozzleLabel(printer, status);
  const hours = printer.total_print_hours;
  const { tone, label } = toneForGcode(status.online, status.gcode_state);
  const model = printer.model && printer.model.trim().toLowerCase() !== printer.name.trim().toLowerCase() ? printer.model : null;

  async function reconnect() {
    try {
      const res = await api.post<ProbeOut>(`/printers/${printer.id}/test`);
      if (res.ok) toast.success(t("card.header.reachable"));
      else toast.error(res.detail);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("card.header.reconnectFailed"));
    }
    void qc.invalidateQueries({ queryKey: ["printers"] });
  }

  return (
    <div className="flex items-center gap-3" data-slot="card-header">
      <img
        src={printerImage(modelKey(printer.model, printer.serial))}
        alt=""
        className={cn(imageClassName, "shrink-0 object-contain", tone === "offline" && "grayscale")}
      />
      <div className="min-w-0 flex-1">
        <Link
          to="/printers/$id"
          params={{ id: String(printer.id) }}
          className="[font-size:calc(15px*var(--pc-s,1))] block truncate font-semibold hover:underline"
        >
          {printer.name}
        </Link>
        <p className="pc-t11 truncate text-muted-foreground">
          {[model, nozzle, hours ? t("card.header.hours", { n: Math.round(hours) }) : null]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </div>
      {extra}
      <StatePill tone={tone} label={label} />
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon-sm" aria-label={t("card.header.menu")}>
            <MoreVerticalIcon />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {canAdmin ? <DropdownMenuItem onSelect={() => void navigate({ to: "/settings" })}>{t("card.header.edit")}</DropdownMenuItem> : null}
          <DropdownMenuItem onSelect={() => void reconnect()}>{t("card.header.reconnect")}</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => void qc.invalidateQueries({ queryKey: ["printers"] })}>
            {t("card.header.forceRefresh")}
          </DropdownMenuItem>
          {canAdmin ? (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onSelect={() => setConfirmDelete(true)}>
                {t("card.header.delete")}
              </DropdownMenuItem>
            </>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={t("card.header.deleteTitle", { name: printer.name })}
        description={t("card.header.deleteDesc")}
        confirmLabel={t("card.header.delete")}
        destructive
        onConfirm={() => del.mutate(printer.id)}
      />
    </div>
  );
}
