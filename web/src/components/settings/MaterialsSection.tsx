/**
 * Materials CRUD (R13c, Settings -> Printer): filament profiles a print can
 * reference in addition to its freeform `filament` text note. Mirrors
 * `CategoriesSection.tsx`'s add/edit-dialog + table-row conventions, but
 * `color` here is a free-form hex string (not a fixed `TagColor` palette
 * key), previewed with `FilamentChip`'s `normalizeHex` helper.
 */
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useCreateMaterial, useDeleteMaterial, useMaterials, useUpdateMaterial } from "@/api/materials";
import { ApiError } from "@/api/client";
import type { MaterialKind, MaterialOut } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { normalizeHex } from "@/components/ui/filament-chip";
import { ColorField } from "@/components/ui/color-field";
import { useDiscardGuard } from "@/components/ui/use-discard-guard";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";

const DEFAULT_KIND: MaterialKind = "PLA";

const MATERIAL_KINDS: MaterialKind[] = ["PLA", "PETG", "ABS", "ASA", "TPU", "Resin", "Other"];

function MaterialFormDialog({ trigger, material }: { trigger: React.ReactNode; material?: MaterialOut }) {
  const { t } = useTranslation();
  const isEdit = material !== undefined;
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(material?.name ?? "");
  const [kind, setKind] = useState<MaterialKind>((material?.kind as MaterialKind | undefined) ?? DEFAULT_KIND);
  const [color, setColor] = useState(material?.color ?? "");
  const [vendor, setVendor] = useState(material?.vendor ?? "");
  const [notes, setNotes] = useState(material?.notes ?? "");

  const createMaterial = useCreateMaterial();
  const updateMaterial = useUpdateMaterial();
  const mutation = isEdit ? updateMaterial : createMaterial;

  const dirty =
    JSON.stringify([name, kind, color, vendor, notes]) !==
    JSON.stringify([material?.name ?? "", material?.kind ?? DEFAULT_KIND, material?.color ?? "", material?.vendor ?? "", material?.notes ?? ""]);

  function reset() {
    setName(material?.name ?? "");
    setKind((material?.kind as MaterialKind | undefined) ?? DEFAULT_KIND);
    setColor(material?.color ?? "");
    setVendor(material?.vendor ?? "");
    setNotes(material?.notes ?? "");
    createMaterial.reset();
    updateMaterial.reset();
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    const payload = {
      name: trimmed,
      kind,
      color: color.trim() || null,
      vendor: vendor.trim() || null,
      notes: notes.trim() || null,
    };
    if (isEdit) {
      updateMaterial.mutate({ id: material.id, payload }, { onSuccess: () => setOpen(false) });
    } else {
      createMaterial.mutate(payload, { onSuccess: () => setOpen(false) });
    }
  }

  const { requestChange, confirm } = useDiscardGuard({
    onOpenChange: (next) => {
      // Re-snapshot on every open and close: fresh props, no stale secrets.
      reset();
      setOpen(next);
    },
    dirty,
    busy: mutation.isPending,
  });

  return (
    <>
    <Dialog open={open} onOpenChange={requestChange}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent dismissable={false}>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>{isEdit ? t("settingsMaterials.editTitle", { name: material.name }) : t("settingsMaterials.addTitle")}</DialogTitle>
            <DialogDescription>
              {t("settingsMaterials.dialogDesc")}
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-4 py-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="material-name">{t("settingsMaterials.name")}</Label>
              <Input
                id="material-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                disabled={mutation.isPending}
                required
                autoFocus
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="material-kind">{t("settingsMaterials.kind")}</Label>
              <Select aria-label={t("settingsMaterials.kind")} value={kind} onValueChange={(value) => setKind(value as MaterialKind)}>
                <SelectTrigger id="material-kind" aria-label={t("settingsMaterials.kind")} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {MATERIAL_KINDS.map((option) => (
                    <SelectItem key={option} value={option}>
                      {option}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="material-color">{t("settingsMaterials.color")}</Label>
              <ColorField
                id="material-color"
                label={t("settingsMaterials.color")}
                value={color}
                onChange={setColor}
                disabled={mutation.isPending}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="material-vendor">{t("settingsMaterials.vendor")}</Label>
              <Input
                id="material-vendor"
                value={vendor}
                onChange={(event) => setVendor(event.target.value)}
                disabled={mutation.isPending}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="material-notes">{t("settingsMaterials.notes")}</Label>
              <Textarea
                id="material-notes"
                rows={2}
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                disabled={mutation.isPending}
              />
            </div>
            {mutation.isError ? (
              <p role="alert" className="text-sm text-destructive">
                {mutation.error instanceof ApiError ? mutation.error.detail : t("settingsMaterials.saveError")}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button type="submit" disabled={name.trim().length === 0 || mutation.isPending}>
              {mutation.isPending ? t("settingsMaterials.saving") : isEdit ? t("settingsMaterials.saveChanges") : t("settingsMaterials.addTitle")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
    {confirm}
    </>
  );
}

function MaterialRow({ material }: { material: MaterialOut }) {
  const { t } = useTranslation();
  const deleteMaterial = useDeleteMaterial();
  const deletingThis = deleteMaterial.isPending && deleteMaterial.variables === material.id;
  const deleteErrorForThis = deleteMaterial.isError && deleteMaterial.variables === material.id;
  const swatch = normalizeHex(material.color);

  return (
    <TableRow>
      <TableCell>
        <span
          aria-hidden="true"
          className="inline-block size-3 rounded-full border border-border/60 align-middle"
          style={swatch ? { backgroundColor: swatch } : undefined}
        />
      </TableCell>
      <TableCell className="font-medium text-foreground">{material.name}</TableCell>
      <TableCell className="text-muted-foreground">{material.kind}</TableCell>
      <TableCell className="text-muted-foreground">{material.vendor ?? "—"}</TableCell>
      <TableCell className="text-muted-foreground">{material.print_count}</TableCell>
      <TableCell className="whitespace-normal">
        <div className="flex flex-wrap items-center gap-1.5">
          <MaterialFormDialog
            material={material}
            trigger={
              <Button type="button" size="sm" variant="outline">
                {t("common.edit")}
              </Button>
            }
          />
          <ConfirmDialog
            trigger={
              <Button type="button" size="sm" variant="destructive" disabled={deletingThis}>
                {deletingThis ? t("settingsMaterials.deleting") : t("common.delete")}
              </Button>
            }
            title={t("settingsMaterials.deleteTitle", { name: material.name })}
            description={
              material.print_count > 0
                ? t("settingsMaterials.deleteDesc", { count: material.print_count })
                : t("settingsMaterials.deleteDescNone")
            }
            confirmLabel={t("common.delete")}
            destructive
            onConfirm={() => deleteMaterial.mutate(material.id)}
          />
        </div>
        {deleteErrorForThis ? (
          <p role="alert" className="mt-1 text-xs text-destructive">
            {deleteMaterial.error instanceof ApiError ? deleteMaterial.error.detail : t("settingsMaterials.deleteError")}
          </p>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

export function MaterialsSection() {
  const { t } = useTranslation();
  const materialsQuery = useMaterials();
  const materials = materialsQuery.data ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settingsMaterials.title")}</CardTitle>
        <CardDescription>
          {t("settingsMaterials.desc")}
        </CardDescription>
        <CardAction>
          <MaterialFormDialog
            trigger={
              <Button type="button" variant="outline">
                {t("settingsMaterials.addTitle")}
              </Button>
            }
          />
        </CardAction>
      </CardHeader>
      <CardContent>
        {materialsQuery.isLoading ? (
          <Skeleton className="h-24 w-full rounded-lg" />
        ) : materialsQuery.isError ? (
          <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed py-8 text-center">
            <div className="space-y-1">
              <p className="text-sm font-medium text-foreground">{t("settingsMaterials.loadError")}</p>
              <p role="alert" className="text-sm text-muted-foreground">
                {materialsQuery.error instanceof ApiError ? materialsQuery.error.detail : t("settingsMaterials.genericError")}
              </p>
            </div>
            <Button type="button" size="sm" onClick={() => void materialsQuery.refetch()}>
              {t("common.retry")}
            </Button>
          </div>
        ) : materials.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("settingsMaterials.empty")}</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead />
                <TableHead>{t("settingsMaterials.name")}</TableHead>
                <TableHead>{t("settingsMaterials.kind")}</TableHead>
                <TableHead>{t("settingsMaterials.vendor")}</TableHead>
                <TableHead>{t("settingsMaterials.colPrints")}</TableHead>
                <TableHead>{t("settingsMaterials.colActions")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {materials.map((material) => (
                <MaterialRow key={material.id} material={material} />
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
