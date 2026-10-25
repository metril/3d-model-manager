/** T3: configure an AMS slot's filament (material, sub-brand, colour, nozzle temps) on the printer. */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useKProfiles, useSelectKProfile } from "@/api/kprofiles";
import { useConfigureSlot, useResetSlotDevice } from "@/api/printerOps";
import type { AmsTray } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { selectIds } from "@/components/print/modals/KProfilesModal";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { normalizeHex } from "@/components/ui/filament-chip";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

/** Bambu generic filament ids (`tray_info_idx`) + default nozzle ranges. */
const MATERIALS: Record<string, { idx: string; min: number; max: number }> = {
  PLA: { idx: "GFL99", min: 190, max: 230 },
  PETG: { idx: "GFG99", min: 220, max: 260 },
  ABS: { idx: "GFB99", min: 240, max: 280 },
  ASA: { idx: "GFB98", min: 240, max: 280 },
  TPU: { idx: "GFU99", min: 200, max: 240 },
  PA: { idx: "GFN99", min: 260, max: 300 },
  PC: { idx: "GFC99", min: 250, max: 290 },
  PVA: { idx: "GFS99", min: 190, max: 230 },
};

export function SlotConfigDialog({
  printerId,
  tray,
  kprofiles = false,
  nozzle = "0.4",
  open,
  onOpenChange,
}: {
  printerId: number;
  tray: AmsTray;
  /** T2: show the K-profile picker (printer has the `kprofiles` capability). */
  kprofiles?: boolean;
  nozzle?: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const { t } = useTranslation();
  const configure = useConfigureSlot(printerId);
  const reset = useResetSlotDevice(printerId);
  const unit = tray.ams_unit ?? 0;
  const kList = useKProfiles(printerId, { nozzle }, { enabled: kprofiles });
  const selectK = useSelectKProfile(printerId);
  const [kChoice, setKChoice] = useState("keep");
  const initial = tray.tray_type ?? tray.material ?? "PLA";
  const [material, setMaterial] = useState(initial in MATERIALS ? initial : "PLA");
  const [subBrand, setSubBrand] = useState("");
  const [colour, setColour] = useState((normalizeHex(tray.color) ?? "#FFFFFF").slice(1));
  const [min, setMin] = useState(String(MATERIALS[material].min));
  const [max, setMax] = useState(String(MATERIALS[material].max));
  const [apply, setApply] = useState(true);
  const swatch = normalizeHex(colour);
  const lo = Number(min);
  const hi = Number(max);
  const valid = !!swatch && Number.isFinite(lo) && Number.isFinite(hi) && lo > 0 && hi >= lo;

  function pickMaterial(m: string) {
    setMaterial(m);
    setMin(String(MATERIALS[m].min));
    setMax(String(MATERIALS[m].max));
  }
  async function save() {
    if (!valid || !swatch) return;
    try {
      const res = await configure.mutateAsync({
        unit,
        slot: tray.tray ?? tray.slot,
        body: {
          tray_info_idx: MATERIALS[material].idx,
          tray_type: material,
          sub_brand: subBrand.trim(),
          color: swatch.slice(1).toUpperCase(),
          nozzle_min: lo,
          nozzle_max: hi,
          apply_device: apply,
        },
      });
      if (kprofiles && kChoice !== "keep") {
        const p = kList.data?.profiles.find((x) => String(x.cali_idx) === kChoice);
        await selectK.mutateAsync({
          ...selectIds(tray),
          cali_idx: kChoice === "default" ? -1 : (p?.cali_idx ?? -1),
          filament_id: p?.filament_id ?? MATERIALS[material].idx,
          nozzle_diameter: nozzle,
        });
      }
      const st = !Array.isArray(res) ? (res as { device?: { status?: string } } | undefined)?.device?.status : undefined;
      if (st === "failed") toast.error(t("printerOps.slot.deviceFailed"));
      else if (st === "skipped" && apply) toast.warning(t("printerOps.slot.deviceSkipped"));
      else toast.success(t("printerOps.slot.saved"));
      onOpenChange(false);
    } catch {
      /* toasted by the hook */
    }
  }
  async function doReset() {
    try {
      await reset.mutateAsync({ unit, slot: tray.tray ?? tray.slot });
      toast.success(t("printerOps.slot.resetDone"));
      onOpenChange(false);
    } catch {
      /* toasted */
    }
  }
  const [confirmReset, setConfirmReset] = useState(false);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("printerOps.slot.title")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <div className="space-y-1">
            <Label>{t("printerOps.slot.material")}</Label>
            <Select value={material} onValueChange={pickMaterial}>
              <SelectTrigger aria-label={t("printerOps.slot.material")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.keys(MATERIALS).map((m) => (
                  <SelectItem key={m} value={m}>
                    {m}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="slot-sub">{t("printerOps.slot.subBrand")}</Label>
            <Input id="slot-sub" value={subBrand} onChange={(e) => setSubBrand(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="slot-color">{t("printerOps.slot.color")}</Label>
            <div className="flex items-center gap-2">
              <span
                data-testid="slot-swatch"
                className="size-8 shrink-0 rounded-md border"
                style={swatch ? { backgroundColor: swatch } : undefined}
              />
              <Input
                id="slot-color"
                value={colour}
                maxLength={9}
                aria-invalid={!swatch}
                onChange={(e) => setColour(e.target.value.replace(/^#/, ""))}
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label htmlFor="slot-min">{t("printerOps.slot.nozzleMin")}</Label>
              <Input id="slot-min" type="number" value={min} onChange={(e) => setMin(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="slot-max">{t("printerOps.slot.nozzleMax")}</Label>
              <Input id="slot-max" type="number" value={max} onChange={(e) => setMax(e.target.value)} />
            </div>
          </div>
          {kprofiles ? (
            <div className="space-y-1">
              <Label>{t("kprofile.slotLabel")}</Label>
              <Select value={kChoice} onValueChange={setKChoice}>
                <SelectTrigger aria-label={t("kprofile.slotLabel")}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="keep">{t("kprofile.keep")}</SelectItem>
                  <SelectItem value="default">{t("kprofile.default")}</SelectItem>
                  {(kList.data?.profiles ?? []).map((p) => (
                    <SelectItem key={p.setting_id} value={String(p.cali_idx)}>
                      {p.name} (K {p.k_value})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {tray.spool ? <p className="text-xs text-muted-foreground">{t("kprofile.autoHint")}</p> : null}
            </div>
          ) : null}
          <div className="flex items-center gap-2">
            <Checkbox id="slot-apply" checked={apply} onCheckedChange={(v) => setApply(v === true)} />
            <Label htmlFor="slot-apply">{t("printerOps.slot.apply")}</Label>
          </div>
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" className="sm:mr-auto" onClick={() => setConfirmReset(true)}>
            {t("printerOps.slot.reset")}
          </Button>
          <Button type="button" disabled={!valid || configure.isPending} onClick={() => void save()}>
            {t("printerOps.slot.save")}
          </Button>
        </DialogFooter>
        <ConfirmDialog
          open={confirmReset}
          onOpenChange={setConfirmReset}
          title={t("printerOps.slot.resetTitle")}
          description={t("printerOps.slot.resetDesc")}
          confirmLabel={t("printerOps.slot.reset")}
          destructive
          onConfirm={() => void doReset()}
        />
      </DialogContent>
    </Dialog>
  );
}
