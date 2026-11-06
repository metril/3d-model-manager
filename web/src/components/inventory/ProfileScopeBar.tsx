/**
 * Scope bar for `/inventory/profiles`: which printer / nozzle / layer height
 * the Profiles, Recipes and Pipelines lists are scoped to, plus a debounced
 * search. State lives in the URL (`profilesSearch.ts`); the nozzle shown is
 * server-resolved (live -> last seen -> assumed) unless the user overrides it.
 */
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { usePrinters } from "@/api/printers";
import { useProfileList } from "@/api/slicing";
import type { NozzleSource } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SwitchRow } from "@/components/ui/switch-row";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { ProfilesSearch } from "@/pages/profilesSearch";

const NOZZLES = [0.2, 0.4, 0.6, 0.8];
const SOURCE_KEY: Record<NozzleSource, string> = {
  override: "profileScope.source.override",
  live: "profileScope.source.live",
  last_seen: "profileScope.source.last_seen",
  assumed: "profileScope.source.assumed",
};

export function ProfileScopeBar({
  search,
  onPatch,
  showLayer,
}: {
  search: ProfilesSearch;
  onPatch: (patch: Partial<ProfilesSearch>) => void;
  showLayer: boolean;
}) {
  const { t } = useTranslation();
  const printers = usePrinters();
  const [qDraft, setQDraft] = useState(search.q ?? "");

  // Default to the first registered printer (also replaces a stale id), written into the URL.
  const firstPrinterId = printers.data?.[0]?.id;
  useEffect(() => {
    const known = printers.data?.some((p) => p.id === search.printer) ?? false;
    if (firstPrinterId != null && (search.printer == null || !known)) onPatch({ printer: firstPrinterId });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search.printer, firstPrinterId, printers.data]);

  // 250 ms debounce into `q`. URL -> draft only when the URL differs from
  // both the draft and the last value we sent, so our own in-flight write
  // (or a stale echo of it) can't clobber newer typing.
  const lastSentQ = useRef(search.q ?? "");
  useEffect(() => {
    if ((search.q ?? "") === qDraft) return;
    const timer = setTimeout(() => {
      lastSentQ.current = qDraft.trim() ? qDraft : "";
      onPatch({ q: qDraft.trim() ? qDraft : undefined });
    }, 250);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qDraft]);
  useEffect(() => {
    const urlQ = search.q ?? "";
    if (urlQ === qDraft || urlQ === lastSentQ.current) return;
    lastSentQ.current = urlQ;
    setQDraft(urlQ);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search.q]);

  // Light probes: the server-resolved nozzle, and the layer heights on offer.
  const scope = { printerId: search.printer, nozzle: search.nozzle };
  const resolved = useProfileList("printer", scope, { full: false });
  const processes = useProfileList("process", scope, { full: false });
  const nozzle = resolved.data?.nozzle_mm ?? search.nozzle ?? null;
  const source: NozzleSource | null = search.nozzle != null ? "override" : (resolved.data?.nozzle_source ?? null);
  const nozzleOptions = [...new Set([...NOZZLES, ...(nozzle != null ? [nozzle] : [])])].sort((a, b) => a - b);
  const layers = [
    ...new Set((processes.data?.items ?? []).map((p) => p.layer_height_mm).filter((l): l is number => l != null)),
  ].sort((a, b) => a - b);
  if (search.layer != null && !layers.includes(search.layer)) layers.push(search.layer);

  return (
    <div className="flex flex-wrap items-end gap-3" data-testid="profile-scope-bar">
      <div className="flex flex-col gap-1">
        <Label htmlFor="scope-printer">{t("profileScope.printer")}</Label>
        <Select
          value={search.printer != null ? String(search.printer) : ""}
          onValueChange={(v) => onPatch({ printer: Number(v), nozzle: undefined })}
        >
          <SelectTrigger id="scope-printer" aria-label={t("profileScope.printer")} className="w-48">
            <SelectValue placeholder={t("profileScope.allPrinters")} />
          </SelectTrigger>
          <SelectContent>
            {(printers.data ?? []).map((p) => (
              <SelectItem key={p.id} value={String(p.id)}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex flex-col gap-1">
        <Label htmlFor="scope-nozzle">{t("profileScope.nozzle")}</Label>
        <div className="flex items-center gap-2">
          <Select
            aria-label={t("profileScope.nozzle")}
            value={search.nozzle != null ? String(search.nozzle) : "auto"}
            onValueChange={(v) => onPatch({ nozzle: v === "auto" ? undefined : Number(v) })}
          >
            <SelectTrigger id="scope-nozzle" aria-label={t("profileScope.nozzle")} className="w-28">
              <SelectValue placeholder={t("profileScope.any")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="auto">{nozzle != null ? t("profileScope.autoMm", { nozzle }) : t("profileScope.auto")}</SelectItem>
              {nozzleOptions.map((n) => (
                <SelectItem key={n} value={String(n)}>
                  {n} mm
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {source ? (
            <Badge variant="outline" data-testid="nozzle-source">
              {t(SOURCE_KEY[source])}
            </Badge>
          ) : null}
        </div>
      </div>

      {showLayer ? (
        <div className="flex flex-col gap-1">
          <Label htmlFor="scope-layer">{t("profileScope.layerHeight")}</Label>
          <Select
            value={search.layer != null ? String(search.layer) : "any"}
            onValueChange={(v) => onPatch({ layer: v === "any" ? undefined : Number(v) })}
          >
            <SelectTrigger id="scope-layer" aria-label={t("profileScope.layerHeight")} className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="any">{t("profileScope.any")}</SelectItem>
              {layers.map((l) => (
                <SelectItem key={l} value={String(l)}>
                  {l} mm
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : null}

      <div className="flex min-w-40 flex-1 flex-col gap-1">
        <Label htmlFor="scope-q">{t("profileScope.search")}</Label>
        <Input
          id="scope-q"
          type="search"
          placeholder={t("profileScope.searchByName")}
          value={qDraft}
          onChange={(e) => setQDraft(e.target.value)}
        />
      </div>
    </div>
  );
}

/** "Showing N of M" + the "Show incompatible" switch shared by each tab header. */
export function ScopeHeader({
  shown,
  total,
  all,
  onAll,
  idPrefix,
}: {
  shown: number;
  /** Omit when the backend can't report an unscoped total. */
  total?: number;
  all?: boolean;
  /** Omit to hide the switch (pipelines have no "show all"). */
  onAll?: (all: boolean) => void;
  idPrefix: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
      <span data-testid={`${idPrefix}-count`}>
        {total != null ? t("profileScope.showingOf", { shown, total }) : t("profileScope.showing", { shown })}
      </span>
      {onAll ? (
        <SwitchRow id={`${idPrefix}-all`} label={t("profileScope.showIncompatible")} checked={all ?? false} onCheckedChange={onAll} />
      ) : null}
    </div>
  );
}

/** Why a row is dimmed, or "Any printer" for universal rows. */
export function ScopeBadges({ universal, reason }: { universal: boolean; reason?: string | null }) {
  const { t } = useTranslation();
  return (
    <>
      {universal ? <Badge variant="secondary">{t("profileScope.anyPrinter")}</Badge> : null}
      {reason ? <Badge variant="destructive">{reason}</Badge> : null}
    </>
  );
}
