/** Colour catalog tab (T5): searchable table, add/edit dialog, reset-to-defaults. */
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import {
  useColorCatalog,
  useCreateColor,
  useDeleteColor,
  usePatchColor,
  useResetColorCatalog,
} from "@/api/spoolAdmin";
import type { ColorCatalogEntry } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FilamentChip } from "@/components/ui/filament-chip";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const MAX_ROWS = 200;

function ColorDialog({ open, onOpenChange, entry }: { open: boolean; onOpenChange: (o: boolean) => void; entry?: ColorCatalogEntry }) {
  const { t } = useTranslation();
  const create = useCreateColor();
  const patch = usePatchColor();
  const mutation = entry ? patch : create;
  const [f, setF] = useState({ manufacturer: "", color_name: "", hex: "", material: "", effect_type: "", extra: "" });

  useEffect(() => {
    if (open) {
      setF({
        manufacturer: entry?.manufacturer ?? "",
        color_name: entry?.color_name ?? "",
        hex: entry?.hex ?? "",
        material: entry?.material ?? "",
        effect_type: entry?.effect_type ?? "",
        extra: (entry?.extra_colors ?? []).join(", "),
      });
      create.reset();
      patch.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function submit(e: FormEvent) {
    e.preventDefault();
    const payload = {
      manufacturer: f.manufacturer.trim(),
      color_name: f.color_name.trim(),
      hex: f.hex.trim(),
      material: f.material.trim() || null,
      effect_type: f.effect_type.trim() || null,
      extra_colors: f.extra.split(",").map((s) => s.trim()).filter(Boolean),
    };
    const done = { onSuccess: () => onOpenChange(false) };
    if (entry) patch.mutate({ id: entry.id, payload }, done);
    else create.mutate(payload, done);
  }
  const bind = (k: keyof typeof f) => ({
    value: f[k],
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setF((s) => ({ ...s, [k]: e.target.value })),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={submit} className="flex flex-col gap-3">
          <DialogHeader>
            <DialogTitle>{entry ? t("spoolAdmin.catalog.editTitle") : t("spoolAdmin.catalog.newTitle")}</DialogTitle>
            <DialogDescription>{t("spoolAdmin.catalog.dialogDesc")}</DialogDescription>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="cc-man">{t("spoolAdmin.catalog.manufacturer")}</Label>
              <Input id="cc-man" required {...bind("manufacturer")} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="cc-name">{t("spoolAdmin.catalog.colorName")}</Label>
              <Input id="cc-name" required {...bind("color_name")} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="cc-hex">{t("spoolAdmin.catalog.hex")}</Label>
              <Input id="cc-hex" required placeholder="#RRGGBB" {...bind("hex")} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="cc-mat">{t("spoolAdmin.f.material")}</Label>
              <Input id="cc-mat" placeholder="PLA" {...bind("material")} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="cc-fx">{t("spoolAdmin.catalog.effect")}</Label>
              <Input id="cc-fx" placeholder={t("spoolAdmin.catalog.effectPlaceholder")} {...bind("effect_type")} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="cc-extra">{t("spoolAdmin.catalog.extra")}</Label>
              <Input id="cc-extra" placeholder="#aabbcc, #ddeeff" {...bind("extra")} />
            </div>
          </div>
          {mutation.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {mutation.error instanceof ApiError ? mutation.error.detail : t("spoolAdmin.common.saveFailed")}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? t("spoolAdmin.common.saving") : t("spoolAdmin.common.save")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function ColorCatalogPanel() {
  const { t } = useTranslation();
  const { can } = useAuth();
  const canWrite = can("inventory:write");
  const catalog = useColorCatalog();
  const del = useDeleteColor();
  const reset = useResetColorCatalog();
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<ColorCatalogEntry | undefined>();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);

  const filtered = useMemo(() => {
    const term = q.trim().toLowerCase();
    const all = catalog.data ?? [];
    if (!term) return all;
    return all.filter((c) => `${c.manufacturer} ${c.color_name} ${c.material ?? ""} ${c.hex}`.toLowerCase().includes(term));
  }, [catalog.data, q]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          aria-label={t("spoolAdmin.catalog.filter")}
          placeholder={t("spoolAdmin.catalog.filter")}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="w-64"
        />
        {canWrite ? (
          <div className="ml-auto flex gap-2">
            <Button type="button" size="sm" variant="outline" onClick={() => setResetOpen(true)}>
              {t("spoolAdmin.catalog.reset")}
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={() => {
                setEditing(undefined);
                setDialogOpen(true);
              }}
            >
              {t("spoolAdmin.catalog.add")}
            </Button>
          </div>
        ) : null}
      </div>
      {catalog.isLoading ? (
        <Skeleton className="h-24 w-full rounded-lg" />
      ) : filtered.length === 0 ? (
        <p className="py-4 text-center text-sm text-muted-foreground">{t("spoolAdmin.catalog.empty")}</p>
      ) : (
        <>
          <Table className="data-table">
            <TableHeader>
              <TableRow>
                <TableHead />
                <TableHead>{t("spoolAdmin.catalog.manufacturer")}</TableHead>
                <TableHead>{t("spoolAdmin.catalog.colorName")}</TableHead>
                <TableHead>{t("spoolAdmin.f.material")}</TableHead>
                <TableHead>{t("spoolAdmin.catalog.hex")}</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.slice(0, MAX_ROWS).map((c) => (
                <TableRow key={c.id}>
                  <TableCell>
                    <div className="flex items-center gap-1">
                      <FilamentChip color={c.hex} />
                      {(c.extra_colors ?? []).map((x) => (
                        <FilamentChip key={x} color={x} />
                      ))}
                    </div>
                  </TableCell>
                  <TableCell>{c.manufacturer}</TableCell>
                  <TableCell className="font-medium">
                    {c.color_name}
                    {c.effect_type ? (
                      <Badge variant="outline" className="ml-2">
                        {c.effect_type}
                      </Badge>
                    ) : null}
                  </TableCell>
                  <TableCell>{c.material ?? "—"}</TableCell>
                  <TableCell className="font-mono text-xs">{c.hex}</TableCell>
                  <TableCell className="text-right">
                    {canWrite ? (
                      <div className="flex justify-end gap-1">
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          aria-label={t("spoolAdmin.common.editNamed", { name: c.color_name })}
                          onClick={() => {
                            setEditing(c);
                            setDialogOpen(true);
                          }}
                        >
                          {t("spoolAdmin.common.edit")}
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          aria-label={t("spoolAdmin.common.deleteNamed", { name: c.color_name })}
                          onClick={() =>
                            del.mutate(c.id, {
                              onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("spoolAdmin.common.deleteFailed")),
                            })
                          }
                        >
                          {t("spoolAdmin.common.delete")}
                        </Button>
                      </div>
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {filtered.length > MAX_ROWS ? (
            <p className="text-xs text-muted-foreground">{t("spoolAdmin.catalog.truncated", { max: MAX_ROWS, total: filtered.length })}</p>
          ) : null}
        </>
      )}
      <ColorDialog open={dialogOpen} onOpenChange={setDialogOpen} entry={editing} />
      <ConfirmDialog
        open={resetOpen}
        onOpenChange={setResetOpen}
        title={t("spoolAdmin.catalog.resetTitle")}
        description={t("spoolAdmin.catalog.resetDesc")}
        confirmLabel={t("spoolAdmin.catalog.reset")}
        destructive
        onConfirm={() =>
          reset.mutate(undefined, {
            onSuccess: () => toast.success(t("spoolAdmin.catalog.resetDone")),
            onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("spoolAdmin.common.saveFailed")),
          })
        }
      />
    </div>
  );
}
