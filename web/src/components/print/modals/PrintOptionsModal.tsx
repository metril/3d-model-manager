/** T3: xcam module toggles + sensitivity, Auto-recovery and AMS backup (auto switch filament). */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useControl } from "@/api/printerControl";
import type { PrintOptionKey, PrinterOut, PrinterStatusOut, XcamSensitivity, XcamState } from "@/api/types";
import { capsOf } from "@/components/print/card/caps";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

const LEVELS: XcamSensitivity[] = ["low", "medium", "high", "never_halt"];

export function PrintOptionsModal({
  printer,
  status,
  canControl,
  open,
  onOpenChange,
}: {
  printer: PrinterOut;
  status?: PrinterStatusOut;
  canControl: boolean;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const { t } = useTranslation();
  const control = useControl(printer.id);
  const modules = capsOf(printer).xcam_modules ?? [];
  const reported = status?.print_options;
  // Optimistic overlay on top of the reported state; reverted when the command fails.
  const [xcamOpt, setXcamOpt] = useState<Record<string, XcamState>>({});
  const [flagOpt, setFlagOpt] = useState<Partial<Record<PrintOptionKey, boolean>>>({});
  const disabled = !canControl || control.pending;

  const xcamOf = (m: string): XcamState => xcamOpt[m] ?? reported?.xcam?.[m] ?? { enable: false, sensitivity: "medium" };
  const flagOf = (k: PrintOptionKey): boolean => flagOpt[k] ?? !!reported?.[k];

  async function setXcam(module: string, next: XcamState) {
    setXcamOpt((o) => ({ ...o, [module]: next }));
    const body: Record<string, unknown> = {
      module,
      enable: next.enable,
      sensitivity: next.sensitivity ?? "medium",
    };
    await control.send("xcam", body);
    // Success or failure: drop the override so the reported status takes over.
    setXcamOpt(({ [module]: _drop, ...rest }) => rest);
  }
  async function setFlag(option: PrintOptionKey, value: boolean) {
    setFlagOpt((o) => ({ ...o, [option]: value }));
    await control.send("print-option", { option, value });
    setFlagOpt(({ [option]: _drop, ...rest }) => rest);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("printerOps.printOptions.title")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          {modules.length === 0 ? (
            <p className="text-muted-foreground">{t("printerOps.printOptions.empty")}</p>
          ) : null}
          {modules.map((m) => {
            const st = xcamOf(m);
            const name = t(`printerOps.printOptions.module.${m}`, { defaultValue: m });
            return (
              <div key={m} className="flex items-center gap-3" data-testid={`xcam-${m}`}>
                <Switch
                  checked={st.enable}
                  disabled={disabled}
                  aria-label={t("printerOps.printOptions.enable", { name })}
                  onCheckedChange={(v) => void setXcam(m, { ...st, enable: v })}
                />
                <span className="min-w-0 flex-1 truncate">{name}</span>
                <Select
                  value={st.sensitivity ?? "medium"}
                  disabled={disabled || !st.enable}
                  onValueChange={(v) => void setXcam(m, { ...st, sensitivity: v as XcamSensitivity })}
                >
                  <SelectTrigger size="sm" className="w-32" aria-label={`${name} ${t("printerOps.printOptions.sensitivity")}`}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {LEVELS.map((l) => (
                      <SelectItem key={l} value={l}>
                        {t(`printerOps.printOptions.${l}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            );
          })}
          <div className="space-y-3 border-t pt-3">
            <div className="flex items-center gap-3">
              <Switch
                id="po-auto-recovery"
                checked={flagOf("auto_recovery")}
                disabled={disabled}
                onCheckedChange={(v) => void setFlag("auto_recovery", v)}
              />
              <Label htmlFor="po-auto-recovery">{t("printerOps.printOptions.autoRecovery")}</Label>
            </div>
            <div className="flex items-center gap-3">
              <Switch
                id="po-auto-switch"
                checked={flagOf("auto_switch_filament")}
                disabled={disabled}
                onCheckedChange={(v) => void setFlag("auto_switch_filament", v)}
              />
              <Label htmlFor="po-auto-switch">{t("printerOps.printOptions.autoSwitch")}</Label>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
