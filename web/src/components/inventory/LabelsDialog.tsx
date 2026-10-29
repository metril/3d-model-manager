/** Label sheet generator (T5): template/page/format controls, live preview, Download. */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import { useDownloadLabels, useLabelPreview } from "@/api/spoolAdmin";
import type { LabelFormat, LabelPage, LabelTemplate } from "@/api/types";
import { Button } from "@/components/ui/button";
import { DialogClose, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export function LabelsDialog({
  ids,
  open,
  onOpenChange,
}: {
  ids: number[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const [template, setTemplate] = useState<LabelTemplate>("tight");
  const [page, setPage] = useState<LabelPage>("a4");
  const [format, setFormat] = useState<LabelFormat>("pdf");
  const preview = useLabelPreview(open ? { ids, template, page } : null);
  const download = useDownloadLabels();
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!preview.data) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(preview.data);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [preview.data]);

  const sel = (id: string, label: string, value: string, set: (v: string) => void, options: string[]) => (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Select aria-label={label} value={value} onValueChange={set}>
        <SelectTrigger id={id} aria-label={label} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o} value={o}>
              {t(`spoolAdmin.labels.opt.${o}`)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );

  return (
    <FormDialog open={open} onOpenChange={onOpenChange} busy={download.isPending} className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("spoolAdmin.labels.title", { count: ids.length })}</DialogTitle>
          <DialogDescription>{t("spoolAdmin.labels.desc")}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-2 sm:grid-cols-[12rem_1fr]">
          <div className="flex flex-col gap-3">
            {sel("label-template", t("spoolAdmin.labels.template"), template, (v) => setTemplate(v as LabelTemplate), ["tight", "roomy"])}
            {sel("label-page", t("spoolAdmin.labels.page"), page, (v) => setPage(v as LabelPage), ["a4", "letter", "roll_62mm"])}
            {sel("label-format", t("spoolAdmin.labels.format"), format, (v) => setFormat(v as LabelFormat), ["pdf", "png"])}
          </div>
          <div className="flex min-h-48 items-center justify-center rounded-md border bg-muted/30 p-2">
            {preview.isPending ? (
              <span className="text-sm text-muted-foreground">{t("spoolAdmin.labels.rendering")}</span>
            ) : preview.isError ? (
              <span role="alert" className="text-sm text-destructive">
                {preview.error instanceof ApiError ? preview.error.detail : t("spoolAdmin.labels.previewFailed")}
              </span>
            ) : previewUrl ? (
              <img src={previewUrl} alt={t("spoolAdmin.labels.previewAlt")} className="max-h-96 max-w-full object-contain" />
            ) : null}
          </div>
        </div>
        {download.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {download.error instanceof ApiError ? download.error.detail : t("spoolAdmin.labels.downloadFailed")}
          </p>
        ) : null}
        <DialogFooter>
          <DialogClose asChild>
            <Button type="button" variant="outline">
            {t("spoolAdmin.common.close")}
          </Button>
          </DialogClose>
          <Button
            type="button"
            disabled={download.isPending || ids.length === 0}
            onClick={() => download.mutate({ req: { ids, template, page }, format })}
          >
            {download.isPending ? t("spoolAdmin.common.saving") : t("spoolAdmin.labels.download")}
          </Button>
        </DialogFooter>
    </FormDialog>
  );
}
