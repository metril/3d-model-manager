/** `/print/plugs`: every smart plug (standalone or linked) with live state,
 * power draw, accumulated energy, a toggle, and add/edit for `settings:write`. */
import { PlugIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useAuth } from "@/api/auth";
import { usePlugs, usePlugToggle } from "@/api/ops";
import { usePrinters } from "@/api/printers";
import { PlugForm } from "@/components/plugs/PlugForm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { PageContainer } from "@/components/ui/page-container";
import { Switch } from "@/components/ui/switch";

export function PlugsPage() {
  const { t } = useTranslation();
  const auth = useAuth();
  const canEdit = auth.can("settings:write");
  const canControl = auth.can("printers:control");
  const plugs = usePlugs();
  const printers = usePrinters();
  const toggle = usePlugToggle();
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const list = plugs.data ?? [];
  const printerName = (id: number | null) => printers.data?.find((p) => p.id === id)?.name;
  const totalW = list.reduce((a, p) => a + (p.last_power_w ?? 0), 0);
  const totalKwh = list.reduce((a, p) => a + p.energy_kwh, 0);

  return (
    <PageContainer width="default">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">{t("plugsPage.title")}</h1>
          <p className="text-sm text-muted-foreground">
            {t("plugsPage.summary", { count: list.length, w: totalW.toFixed(0), kwh: totalKwh.toFixed(2) })}
          </p>
        </div>
        {canEdit && editing === null ? (
          <Button type="button" size="sm" onClick={() => setEditing("new")}>
            {t("plugsPage.add")}
          </Button>
        ) : null}
      </div>
      {editing === "new" ? (
        <Card>
          <CardContent className="pt-4">
            <PlugForm onDone={() => setEditing(null)} />
          </CardContent>
        </Card>
      ) : null}
      {list.length === 0 && editing === null ? <p className="text-sm text-muted-foreground">{t("plugsPage.empty")}</p> : null}
      {list.map((p) => {
        const on = p.last_state === "on";
        const label = p.name || t("plugsPage.fallbackName", { id: p.id });
        return (
          <Card key={p.id} data-testid="plug-card">
            <CardContent className="space-y-3 pt-4">
              {editing === p.id ? (
                <PlugForm plug={p} onDone={() => setEditing(null)} />
              ) : (
                <div className="flex flex-wrap items-center gap-3 text-sm">
                  <PlugIcon className="size-4 text-muted-foreground" />
                  <span className="font-medium">{label}</span>
                  <span className="text-muted-foreground">
                    {p.kind}
                    {p.printer_id != null
                      ? ` - ${printerName(p.printer_id) ?? t("plugsPage.printerId", { id: p.printer_id })}${p.controls_printer_power ? "" : ` ${t("plugsPage.accessory")}`}`
                      : ` - ${t("plugsPage.standalone")}`}
                  </span>
                  {p.orphaned ? (
                    <Badge variant="outline" className="border-warning bg-warning/10 text-warning">
                      {t("plugsPage.orphaned")}
                    </Badge>
                  ) : null}
                  <span className="font-medium">{p.last_state ? (on ? t("plugsPage.on") : t("plugsPage.off")) : t("plugsPage.unknown")}</span>
                  <span className="text-muted-foreground">
                    {p.last_power_w != null ? `${p.last_power_w.toFixed(0)} W - ` : ""}
                    {p.energy_kwh.toFixed(2)} kWh
                  </span>
                  <div className="ml-auto flex items-center gap-2">
                    {canEdit ? (
                      <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(p.id)}>
                        {p.orphaned ? t("plugsPage.relink") : t("common.edit")}
                      </Button>
                    ) : null}
                    <Switch
                      aria-label={t("plugsPage.power", { label })}
                      checked={on}
                      disabled={!canControl || (p.orphaned && !canEdit) || toggle.isPending}
                      onCheckedChange={(v) => toggle.mutate({ id: p.id, on: v })}
                    />
                  </div>
                  {p.last_error ? (
                    <p role="alert" className="w-full text-xs text-destructive">
                      {p.last_error}
                    </p>
                  ) : null}
                </div>
              )}
            </CardContent>
          </Card>
        );
      })}
    </PageContainer>
  );
}
