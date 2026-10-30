/** Label sheet generator (T5): template/page/format controls, live preview, Download. */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import { useDownloadLabels, useLabelPreview } from "@/api/spoolAdmin";
import { LABEL_FIELDS, type LabelField, type LabelFormat, type LabelPage, type LabelTemplate } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { DialogClose, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export function LabelsDialog({
  ids,
  open,
  onOpenChange,
  kind = "spool",
}: {
  ids: number[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** "location" prints location labels (QR -> /scan/location/<id>); no live preview. */
  kind?: "spool" | "location";
}) {
  const { t } = useTranslation();
  const [template, setTemplate] = useState<LabelTemplate>("tight");
  const [page, setPage] = useState<LabelPage>("a4");
  const [format, setFormat] = useState<LabelFormat>("pdf");
  const [fields, setFields] = useState<LabelField[]>(LABEL_FIELDS);
  const [offset, setOffset] = useState(0);
  const isSpool = kind === "spool";
  const sheet = page !== "roll_62mm";
  const req = { ids, template, page, ...(isSpool ? { fields } : {}), offset: sheet ? offset : 0 };
  const preview = useLabelPreview(open && isSpool ? req : null);
  const download = useDownloadLabels(kind);
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
          <DialogTitle>{t(isSpool ? "spoolAdmin.labels.title" : "spoolAdmin.labels.locTitle", { count: ids.length })}</DialogTitle>
          <DialogDescription>{t(isSpool ? "spoolAdmin.labels.desc" : "spoolAdmin.labels.locDesc")}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-2 sm:grid-cols-[12rem_1fr]">
          <div className="flex flex-col gap-3">
            {sel("label-template", t("spoolAdmin.labels.template"), template, (v) => setTemplate(v as LabelTemplate), ["tight", "roomy"])}
            {sel("label-page", t("spoolAdmin.labels.page"), page, (v) => setPage(v as LabelPage), ["a4", "letter", "roll_62mm"])}
            {sel("label-format", t("spoolAdmin.labels.format"), format, (v) => setFormat(v as LabelFormat), ["pdf", "png"])}
            {sheet ? (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="label-offset">{t("spoolAdmin.labels.offset")}</Label>
                <Input
                  id="label-offset"
                  type="number"
                  min={0}
                  max={299}
                  value={offset}
                  onChange={(e) => setOffset(Math.max(0, Math.min(299, Number(e.target.value) || 0)))}
                />
              </div>
            ) : null}
            {isSpool ? (
              <fieldset className="flex flex-col gap-1.5">
                <legend className="mb-1 text-sm font-medium">{t("spoolAdmin.labels.contents")}</legend>
                {LABEL_FIELDS.map((f) => (
                  <div key={f} className="flex items-center gap-2">
                    <Checkbox
                      id={`label-field-${f}`}
                      checked={fields.includes(f)}
                      onCheckedChange={(on) =>
                        setFields((prev) => (on ? LABEL_FIELDS.filter((x) => x === f || prev.includes(x)) : prev.filter((x) => x !== f)))
                      }
                    />
                    <Label htmlFor={`label-field-${f}`} className="font-normal">
                      {t(`spoolAdmin.labels.field.${f}`)}
                    </Label>
                  </div>
                ))}
              </fieldset>
            ) : null}
          </div>
          <div className="flex min-h-48 items-center justify-center rounded-md border bg-muted/30 p-2">
            {!isSpool ? (
              <span className="text-sm text-muted-foreground">{t("spoolAdmin.labels.locPreview")}</span>
            ) : preview.isPending ? (
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
            onClick={() => download.mutate({ req, format })}
          >
            {download.isPending ? t("spoolAdmin.common.saving") : t("spoolAdmin.labels.download")}
          </Button>
        </DialogFooter>
    </FormDialog>
  );
}
