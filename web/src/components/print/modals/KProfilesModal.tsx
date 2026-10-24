/** T2: pressure-advance (K-profile) manager: list, edit, add, delete, link to spool, apply to tray. */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import i18n from "@/i18n";

import { useAuth } from "@/api/auth";
import { api } from "@/api/client";
import {
  mergeSpoolLink,
  profileToLink,
  usePutSpoolKProfiles,
  useDeleteKProfile,
  useKProfileNote,
  useKProfiles,
  useRefreshKProfiles,
  useSaveKProfile,
  useSelectKProfile,
} from "@/api/kprofiles";
import { useSpools } from "@/api/spools";
import type { AmsTray, KProfile, PrinterOut, PrinterStatusOut, SpoolKProfile } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { capsOf, isBusy } from "@/components/print/card/caps";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export const NOZZLES = ["0.2", "0.4", "0.6", "0.8"];

export function printerNozzle(printer: PrinterOut): string {
  const n = Number(printer.options?.nozzle_diameter);
  return Number.isFinite(n) && n > 0 ? n.toFixed(1) : "0.4";
}

/** Current nozzle first, then the rest. */
export function nozzleOrder(current: string): string[] {
  return [current, ...NOZZLES.filter((n) => n !== current)];
}

export function trayLabel(tr: AmsTray): string {
  return tr.ams_unit === 255 ? i18n.t("kprofile.ext") : `A${(tr.ams_unit ?? 0) + 1}-${(tr.tray ?? tr.slot) + 1}`;
}

/** K 0-2 inclusive, n finite >= 0; returns an error key or null. */
export function validateK(k: string, n: string): "k" | "n" | null {
  const kv = Number(k);
  if (k.trim() === "" || !Number.isFinite(kv) || kv < 0 || kv > 2) return "k";
  const nv = Number(n);
  if (n.trim() === "" || !Number.isFinite(nv) || nv < 0 || nv > 2) return "n";
  return null;
}

const round6 = (v: number) => Number(v.toFixed(6));

function PickDialog({
  title,
  label,
  items,
  confirm,
  onConfirm,
  onClose,
}: {
  title: string;
  label: string;
  items: { value: string; label: string }[];
  confirm: string;
  onConfirm: (value: string) => void;
  onClose: () => void;
}) {
  const [value, setValue] = useState("");
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <Select value={value} onValueChange={setValue}>
          <SelectTrigger aria-label={label}>
            <SelectValue placeholder={label} />
          </SelectTrigger>
          <SelectContent>
            {items.map((i) => (
              <SelectItem key={i.value} value={i.value}>
                {i.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <DialogFooter>
          <Button type="button" disabled={!value} onClick={() => onConfirm(value)}>
            {confirm}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EditRow({
  profile,
  onCancel,
  onSave,
  pending,
}: {
  profile: KProfile | null;
  onCancel: () => void;
  onSave: (v: { name: string; filament_id: string; k: number; n: number }) => void;
  pending: boolean;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(profile?.name ?? "");
  const [fil, setFil] = useState(profile?.filament_id ?? "");
  const [k, setK] = useState(profile ? String(profile.k_value) : "0.020000");
  const [n, setN] = useState(profile ? String(profile.n_coef) : "0");
  const bad = validateK(k, n);
  const ok = !bad && name.trim() !== "" && fil.trim() !== "";
  return (
    <tr data-testid="kprofile-edit" className="border-b bg-muted/40">
      <td className="p-1">
        <Input aria-label={t("kprofile.name")} maxLength={64} value={name} onChange={(e) => setName(e.target.value)} />
      </td>
      <td className="p-1">
        <Input aria-label={t("kprofile.filament")} maxLength={32} value={fil} disabled={!!profile} onChange={(e) => setFil(e.target.value)} />
      </td>
      <td className="p-1">
        <Input aria-label="K" aria-invalid={bad === "k"} inputMode="decimal" value={k} onChange={(e) => setK(e.target.value)} />
      </td>
      <td className="p-1">
        <Input aria-label="n" aria-invalid={bad === "n"} inputMode="decimal" value={n} onChange={(e) => setN(e.target.value)} />
      </td>
      <td className="p-1 text-xs text-destructive" colSpan={2}>
        {bad === "k" ? t("kprofile.invalidK") : bad === "n" ? t("kprofile.invalidN") : null}
      </td>
      <td className="p-1">
        <div className="flex gap-1">
          <Button
            type="button"
            size="xs"
            disabled={!ok || pending}
            onClick={() => onSave({ name: name.trim(), filament_id: fil.trim(), k: round6(Number(k)), n: round6(Number(n)) })}
          >
            {t("kprofile.save")}
          </Button>
          <Button type="button" size="xs" variant="outline" onClick={onCancel}>
            {t("kprofile.cancel")}
          </Button>
        </div>
      </td>
    </tr>
  );
}

function NoteCell({ profile, disabled, onCommit }: { profile: KProfile; disabled: boolean; onCommit: (note: string) => void }) {
  const { t } = useTranslation();
  const [v, setV] = useState(profile.note ?? "");
  useEffect(() => setV(profile.note ?? ""), [profile.note]);
  return (
    <Input
      aria-label={t("kprofile.noteFor", { name: profile.name })}
      value={v}
      disabled={disabled}
      placeholder={t("kprofile.note")}
      onChange={(e) => setV(e.target.value)}
      onBlur={() => v.trim() !== (profile.note ?? "") && onCommit(v)}
    />
  );
}

export function KProfilesModal({
  printer,
  status,
  canControl,
  tray,
  open,
  onOpenChange,
}: {
  printer: PrinterOut;
  status?: PrinterStatusOut;
  canControl: boolean;
  /** Opened from a slot: highlight its current profile + one-click "Set for this tray". */
  tray?: AmsTray;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const { t } = useTranslation();
  const { can } = useAuth();
  const dual = !!capsOf(printer).dual_nozzle;
  const current = printerNozzle(printer);
  const [nozzle, setNozzle] = useState(current);
  const [extruder, setExtruder] = useState(0);
  const scope = { nozzle, extruder: dual ? extruder : undefined };
  const list = useKProfiles(printer.id, scope);
  const refresh = useRefreshKProfiles(printer.id);
  const save = useSaveKProfile(printer.id);
  const del = useDeleteKProfile(printer.id);
  const select = useSelectKProfile(printer.id);
  const noteM = useKProfileNote(printer.id);
  const spools = useSpools();
  const putSpool = usePutSpoolKProfiles();
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [confirmDel, setConfirmDel] = useState<KProfile | null>(null);
  const [linking, setLinking] = useState<KProfile | null>(null);
  const [applying, setApplying] = useState<KProfile | null>(null);

  const busy = isBusy(status?.gcode_state);
  const locked = !canControl || busy;
  const trays = status?.trays ?? [];
  const profiles = list.data?.profiles ?? [];
  const canLink = can("inventory:write");

  function usedBy(p: KProfile) {
    return trays.filter((tr) => tr.cali_idx != null && tr.cali_idx >= 0 && tr.cali_idx === p.cali_idx);
  }
  function selectBody(tr: AmsTray, p: KProfile) {
    return {
      ams_id: tr.ams_unit ?? 0,
      tray_id: tr.tray ?? tr.slot,
      cali_idx: p.cali_idx,
      filament_id: p.filament_id,
      nozzle_diameter: nozzle,
    };
  }
  async function applyTo(tr: AmsTray, p: KProfile) {
    try {
      await select.mutateAsync(selectBody(tr, p));
      toast.success(t("kprofile.applied", { name: p.name, tray: trayLabel(tr) }));
    } catch {
      /* toasted */
    }
  }
  async function saveRow(p: KProfile | null, v: { name: string; filament_id: string; k: number; n: number }) {
    try {
      await save.mutateAsync({
        name: v.name,
        filament_id: v.filament_id,
        k_value: v.k,
        n_coef: v.n,
        nozzle_diameter: nozzle,
        extruder_id: p?.extruder_id ?? (dual ? extruder : 0),
        nozzle_id: p?.nozzle_id ?? undefined,
        ...(p ? { setting_id: p.setting_id, cali_idx: p.cali_idx } : {}),
      });
      setEditing(null);
    } catch {
      /* toasted */
    }
  }
  async function linkSpool(p: KProfile, spoolId: number) {
    try {
      const existing = await api.get<SpoolKProfile[]>(`/spools/${spoolId}/kprofiles`);
      await putSpool.mutateAsync({ spoolId, links: mergeSpoolLink(existing, profileToLink(printer.id, p)) });
      toast.success(t("kprofile.linked"));
      setLinking(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("kprofile.linkFailed"));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t("kprofile.title")}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-wrap items-center gap-2" role="tablist" aria-label={t("kprofile.nozzle")}>
          {nozzleOrder(current).map((n) => (
            <Button
              key={n}
              type="button"
              role="tab"
              aria-selected={n === nozzle}
              size="xs"
              variant={n === nozzle ? "default" : "outline"}
              onClick={() => {
                setNozzle(n);
                setEditing(null);
              }}
            >
              {n} mm
            </Button>
          ))}
          {dual ? (
            <div className="ml-2 flex gap-2" role="tablist" aria-label={t("kprofile.extruderTabs")}>
              {[0, 1].map((e) => (
                <Button
                  key={e}
                  type="button"
                  role="tab"
                  aria-selected={e === extruder}
                  size="xs"
                  variant={e === extruder ? "default" : "outline"}
                  onClick={() => setExtruder(e)}
                >
                  {t("kprofile.extruder", { n: e })}
                </Button>
              ))}
            </div>
          ) : null}
          <div className="ml-auto flex gap-2">
            <Button type="button" size="xs" variant="outline" disabled={refresh.isPending} onClick={() => refresh.mutate(scope)}>
              {t("kprofile.refresh")}
            </Button>
            <Button type="button" size="xs" disabled={locked || editing === "new"} onClick={() => setEditing("new")}>
              {t("kprofile.add")}
            </Button>
          </div>
        </div>
        {busy ? <p className="text-xs text-muted-foreground">{t("kprofile.busyHint")}</p> : null}
        {list.isLoading ? (
          <p className="text-sm text-muted-foreground">{t("kprofile.waiting")}</p>
        ) : list.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {t("kprofile.loadFailed")}
          </p>
        ) : null}
        {!list.isLoading && !list.isError && profiles.length === 0 && editing !== "new" ? (
          <p className="text-sm text-muted-foreground">{t("kprofile.empty", { nozzle })}</p>
        ) : null}
        {profiles.length > 0 || editing === "new" ? (
          <div className="max-h-[60vh] overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="p-1">{t("kprofile.name")}</th>
                  <th className="p-1">{t("kprofile.filament")}</th>
                  <th className="p-1">K</th>
                  <th className="p-1">n</th>
                  <th className="p-1">{t("kprofile.note")}</th>
                  <th className="p-1">{t("kprofile.usedBy")}</th>
                  <th className="p-1" />
                </tr>
              </thead>
              <tbody>
                {editing === "new" ? (
                  <EditRow profile={null} pending={save.isPending} onCancel={() => setEditing(null)} onSave={(v) => void saveRow(null, v)} />
                ) : null}
                {profiles.map((p) =>
                  editing === p.setting_id ? (
                    <EditRow key={p.setting_id} profile={p} pending={save.isPending} onCancel={() => setEditing(null)} onSave={(v) => void saveRow(p, v)} />
                  ) : (
                    <tr
                      key={p.setting_id}
                      data-testid={`kprofile-${p.cali_idx}`}
                      className={`border-b ${tray && tray.cali_idx === p.cali_idx ? "bg-primary/10" : ""}`}
                    >
                      <td className="p-1">
                        {p.name}
                        {tray && tray.cali_idx === p.cali_idx ? (
                          <Badge variant="secondary" className="ml-1">
                            {t("kprofile.current")}
                          </Badge>
                        ) : null}
                      </td>
                      <td className="p-1 font-mono text-xs">{p.filament_id}</td>
                      <td className="p-1 font-mono tabular-nums">{p.k_value.toFixed(6)}</td>
                      <td className="p-1 font-mono tabular-nums">{p.n_coef}</td>
                      <td className="p-1">
                        <NoteCell profile={p} disabled={locked} onCommit={(note) => noteM.mutate({ settingId: p.setting_id, note })} />
                      </td>
                      <td className="p-1 text-xs">{usedBy(p).map(trayLabel).join(", ")}</td>
                      <td className="p-1">
                        <div className="flex flex-wrap gap-1">
                          {tray ? (
                            <Button type="button" size="xs" disabled={locked || select.isPending} onClick={() => void applyTo(tray, p)}>
                              {t("kprofile.setForTray")}
                            </Button>
                          ) : null}
                          <Button type="button" size="xs" variant="outline" disabled={locked} onClick={() => setEditing(p.setting_id)}>
                            {t("kprofile.edit")}
                          </Button>
                          <Button type="button" size="xs" variant="outline" disabled={locked} onClick={() => setConfirmDel(p)}>
                            {t("kprofile.delete")}
                          </Button>
                          {canLink ? (
                            <Button type="button" size="xs" variant="outline" onClick={() => setLinking(p)}>
                              {t("kprofile.linkSpool")}
                            </Button>
                          ) : null}
                          {!tray && trays.length > 0 ? (
                            <Button type="button" size="xs" variant="outline" disabled={locked} onClick={() => setApplying(p)}>
                              {t("kprofile.applyTray")}
                            </Button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
        ) : null}
        <ConfirmDialog
          open={confirmDel != null}
          onOpenChange={(o) => !o && setConfirmDel(null)}
          title={t("kprofile.deleteTitle")}
          description={t("kprofile.deleteDesc", { name: confirmDel?.name ?? "" })}
          confirmLabel={t("kprofile.delete")}
          destructive
          onConfirm={() => {
            if (confirmDel) del.mutate({ profile: confirmDel, extruder: dual ? extruder : undefined });
            setConfirmDel(null);
          }}
        />
        {linking ? (
          <PickDialog
            title={t("kprofile.linkSpool")}
            label={t("kprofile.pickSpool")}
            confirm={t("kprofile.link")}
            items={(spools.data ?? []).map((s) => ({
              value: String(s.id),
              label: `#${s.id} ${[s.brand, s.material_type, s.color_name].filter(Boolean).join(" ")}`,
            }))}
            onClose={() => setLinking(null)}
            onConfirm={(v) => void linkSpool(linking, Number(v))}
          />
        ) : null}
        {applying ? (
          <PickDialog
            title={t("kprofile.applyTray")}
            label={t("kprofile.pickTray")}
            confirm={t("kprofile.apply")}
            items={trays.map((tr) => ({ value: String(trays.indexOf(tr)), label: `${trayLabel(tr)} ${tr.tray_type ?? tr.material ?? ""}`.trim() }))}
            onClose={() => setApplying(null)}
            onConfirm={(v) => {
              void applyTo(trays[Number(v)], applying);
              setApplying(null);
            }}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
