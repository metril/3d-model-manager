/** T2: per-printer pressure-advance links for a spool (edit mode of SpoolForm). */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  mergeSpoolLink,
  profileToLink,
  useDeleteSpoolKProfile,
  useKProfiles,
  usePutSpoolKProfiles,
  useSpoolKProfiles,
} from "@/api/kprofiles";
import { usePrinters } from "@/api/printers";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const NOZZLES = ["0.2", "0.4", "0.6", "0.8"];

function AddFromPrinter({ spoolId, onDone }: { spoolId: number; onDone: () => void }) {
  const { t } = useTranslation();
  const printers = usePrinters();
  const links = useSpoolKProfiles(spoolId);
  const put = usePutSpoolKProfiles();
  const [printerId, setPrinterId] = useState("");
  const [nozzle, setNozzle] = useState("0.4");
  const pid = printerId ? Number(printerId) : 0;
  const list = useKProfiles(pid, { nozzle }, { enabled: pid > 0 });
  const capable = (printers.data ?? []).filter((p) => p.capabilities?.kprofiles);
  return (
    <div className="space-y-2 rounded-md border p-2" data-testid="kprofile-add-panel">
      <div className="flex flex-wrap gap-2">
        <Select value={printerId} onValueChange={setPrinterId}>
          <SelectTrigger aria-label={t("kprofile.pickPrinter")} className="w-44">
            <SelectValue placeholder={t("kprofile.pickPrinter")} />
          </SelectTrigger>
          <SelectContent>
            {capable.map((p) => (
              <SelectItem key={p.id} value={String(p.id)}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={nozzle} onValueChange={setNozzle}>
          <SelectTrigger aria-label={t("kprofile.nozzle")} className="w-28">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {NOZZLES.map((n) => (
              <SelectItem key={n} value={n}>
                {n} mm
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button type="button" size="xs" variant="outline" onClick={onDone}>
          {t("kprofile.cancel")}
        </Button>
      </div>
      {pid > 0 && list.isLoading ? <p className="text-xs text-muted-foreground">{t("kprofile.waiting")}</p> : null}
      {pid > 0 && list.isError ? <p className="text-xs text-destructive">{t("kprofile.loadFailed")}</p> : null}
      <ul className="space-y-1">
        {(list.data?.profiles ?? []).map((p) => (
          <li key={p.setting_id} className="flex items-center gap-2 text-sm">
            <span className="flex-1">
              {p.name} <span className="font-mono text-xs text-muted-foreground">K {p.k_value}</span>
            </span>
            <Button
              type="button"
              size="xs"
              disabled={put.isPending}
              onClick={() =>
                put.mutate({ spoolId, links: mergeSpoolLink(links.data ?? [], profileToLink(pid, p)) }, { onSuccess: onDone })
              }
            >
              {t("kprofile.add")}
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function SpoolKProfilesSection({ spoolId, canWrite }: { spoolId: number; canWrite: boolean }) {
  const { t } = useTranslation();
  const links = useSpoolKProfiles(spoolId);
  const printers = usePrinters();
  const del = useDeleteSpoolKProfile(spoolId);
  const [adding, setAdding] = useState(false);
  const name = (id: number) => printers.data?.find((p) => p.id === id)?.name ?? `#${id}`;
  return (
    <div className="flex flex-col gap-1.5" data-testid="spool-kprofiles">
      <Label>{t("kprofile.spoolTitle")}</Label>
      {links.data?.length ? (
        <ul className="space-y-1">
          {links.data.map((l) => (
            <li key={`${l.printer_id}-${l.extruder}-${l.nozzle_diameter}`} className="flex items-center gap-2 text-sm">
              <span className="flex-1">
                {name(l.printer_id)} · {t("kprofile.extruder", { n: l.extruder })} · {l.nozzle_diameter} mm · {l.name}{" "}
                <span className="font-mono text-xs text-muted-foreground">K {l.k_value}</span>
              </span>
              {canWrite ? (
                <Button type="button" size="xs" variant="outline" disabled={del.isPending} onClick={() => del.mutate(l)}>
                  {t("kprofile.remove")}
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted-foreground">{t("kprofile.spoolEmpty")}</p>
      )}
      {canWrite && !adding ? (
        <Button type="button" size="xs" variant="outline" className="self-start" onClick={() => setAdding(true)}>
          {t("kprofile.addFromPrinter")}
        </Button>
      ) : null}
      {adding ? <AddFromPrinter spoolId={spoolId} onDone={() => setAdding(false)} /> : null}
    </div>
  );
}
