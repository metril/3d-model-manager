/**
 * CSV import (T5): pick a file -> server dry-run -> per-row diff table ->
 * Commit. The commit token expires server-side (410); we then prompt to
 * re-run the dry-run with the same file.
 */
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useImportCommit, useImportDryRun } from "@/api/spoolAdmin";
import type { CsvImportPreview } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export function CsvImportDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation();
  const dry = useImportDryRun();
  const commit = useImportCommit();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<CsvImportPreview | null>(null);
  const [expired, setExpired] = useState(false);

  useEffect(() => {
    if (open) {
      setFile(null);
      setPreview(null);
      setExpired(false);
      dry.reset();
      commit.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function runDry(f: File) {
    setExpired(false);
    setPreview(null);
    dry.mutate(f, { onSuccess: setPreview });
  }

  function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0] ?? null;
    setFile(f);
    if (f) runDry(f);
  }

  function doCommit() {
    if (!preview) return;
    commit.mutate(preview.token, {
      onSuccess: (r) => {
        toast.success(t("spoolAdmin.csv.done", { created: r.created, updated: r.updated }));
        onOpenChange(false);
      },
      onError: (err) => {
        if (err instanceof ApiError && err.status === 410) {
          setExpired(true);
          setPreview(null);
        }
      },
    });
  }

  const blocked = !preview || preview.summary.create + preview.summary.update === 0;
  const error = dry.error ?? (commit.error && !expired ? commit.error : null);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t("spoolAdmin.csv.title")}</DialogTitle>
          <DialogDescription>{t("spoolAdmin.csv.desc")}</DialogDescription>
        </DialogHeader>
        <div className="flex max-h-[60vh] flex-col gap-3 overflow-y-auto py-2">
          <div className="flex items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".csv,text/csv"
              aria-label={t("spoolAdmin.csv.file")}
              onChange={onPick}
              className="text-sm"
            />
          </div>
          {dry.isPending ? <p className="text-sm text-muted-foreground">{t("spoolAdmin.csv.checking")}</p> : null}
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error instanceof ApiError ? error.detail : t("spoolAdmin.csv.failed")}
            </p>
          ) : null}
          {expired ? (
            <div role="alert" className="flex items-center justify-between gap-2 rounded-md border border-amber-500/40 p-2 text-sm">
              <span>{t("spoolAdmin.csv.expired")}</span>
              {file ? (
                <Button type="button" size="sm" variant="outline" onClick={() => runDry(file)}>
                  {t("spoolAdmin.csv.recheck")}
                </Button>
              ) : null}
            </div>
          ) : null}
          {preview ? (
            <>
              <p className="text-sm" data-testid="csv-summary">
                {t("spoolAdmin.csv.summary", {
                  create: preview.summary.create,
                  update: preview.summary.update,
                  error: preview.summary.error,
                })}
              </p>
              <Table className="data-table">
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("spoolAdmin.csv.line")}</TableHead>
                    <TableHead>{t("spoolAdmin.csv.action")}</TableHead>
                    <TableHead>{t("spoolAdmin.csv.messages")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {preview.rows.map((r) => (
                    <TableRow key={r.line}>
                      <TableCell className="font-mono text-xs">{r.line}</TableCell>
                      <TableCell>
                        <Badge variant={r.action === "error" ? "destructive" : r.action === "create" ? "default" : "secondary"}>
                          {t(`spoolAdmin.csv.act.${r.action}`)}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs">
                        {r.errors.map((m, i) => (
                          <div key={`e${i}`} className="text-destructive">
                            {m}
                          </div>
                        ))}
                        {r.warnings.map((m, i) => (
                          <div key={`w${i}`} className="text-muted-foreground">
                            {m}
                          </div>
                        ))}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </>
          ) : null}
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            {t("spoolAdmin.common.cancel")}
          </Button>
          <Button type="button" disabled={blocked || commit.isPending} onClick={doCommit}>
            {commit.isPending ? t("spoolAdmin.common.saving") : t("spoolAdmin.csv.commit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
