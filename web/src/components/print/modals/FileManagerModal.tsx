/** T3: printer SD-card browser: breadcrumb nav, download, delete, storage bar, print `.3mf`/`.gcode.3mf`. */
import { DownloadIcon, FileIcon, FolderIcon, PlayIcon, Trash2Icon } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { fileDownloadUrl, useDeletePrinterFile, useFileStorage, usePrinterFiles, usePrintSdFile } from "@/api/printerOps";
import type { RemoteFile } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

function isPrintable(name: string): boolean {
  const n = name.toLowerCase();
  return n.endsWith(".3mf");
}

function fmtSize(n: number | null | undefined): string {
  if (n == null) return "—";
  const units = ["byte", "kilobyte", "megabyte", "gigabyte", "terabyte"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return new Intl.NumberFormat(undefined, { style: "unit", unit: units[i], unitDisplay: "narrow", maximumFractionDigits: v >= 100 || i === 0 ? 0 : 1 }).format(v);
}

export function FileManagerModal({
  printerId,
  canControl,
  sdPrint,
  open,
  onOpenChange,
  previewSlot,
}: {
  printerId: number;
  canControl: boolean;
  /** `caps.sd_print` */
  sdPrint: boolean;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  /** Reserved for the G-code preview (T8b): rendered beside the list for the selected file. */
  previewSlot?: (file: RemoteFile) => ReactNode;
}) {
  const { t } = useTranslation();
  const [path, setPath] = useState("/");
  const [selected, setSelected] = useState<RemoteFile | null>(null);
  const [toDelete, setToDelete] = useState<RemoteFile | null>(null);
  const [toPrint, setToPrint] = useState<RemoteFile | null>(null);
  const goTo = (p: string) => {
    setSelected(null);
    setPath(p);
  };
  const files = usePrinterFiles(printerId, path, open);
  const storage = useFileStorage(printerId, open);
  const del = useDeletePrinterFile(printerId);
  const print = usePrintSdFile(printerId);

  const crumbs = path.split("/").filter(Boolean);
  const entries = [...(files.data ?? [])].sort((a, b) => Number(b.is_dir) - Number(a.is_dir) || a.name.localeCompare(b.name));
  const st = storage.data;
  const pct = st?.total && st.used != null ? Math.min(100, Math.round((st.used / st.total) * 100)) : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>{t("printerOps.files.title")}</DialogTitle>
        </DialogHeader>
        <nav aria-label="breadcrumb" className="flex flex-wrap items-center gap-1 text-sm">
          <Button type="button" variant="ghost" size="xs" onClick={() => goTo("/")}>
            {t("printerOps.files.root")}
          </Button>
          {crumbs.map((c, i) => (
            <span key={`${i}-${c}`} className="flex items-center gap-1">
              <span className="text-muted-foreground">/</span>
              <Button type="button" variant="ghost" size="xs" onClick={() => goTo(`/${crumbs.slice(0, i + 1).join("/")}`)}>
                {c}
              </Button>
            </span>
          ))}
        </nav>
        {pct != null && st?.total ? (
          <div className="space-y-1" data-testid="storage-bar">
            <div role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} className="h-1.5 overflow-hidden rounded-full bg-muted">
              <div className="h-full bg-foreground/60" style={{ width: `${pct}%` }} />
            </div>
            <p className="text-xs text-muted-foreground">
              {t("printerOps.files.storage", { used: fmtSize(st.used), total: fmtSize(st.total) })}
            </p>
          </div>
        ) : null}
        <div className="flex gap-3">
          <div className="max-h-[50vh] min-w-0 flex-1 overflow-y-auto">
            {files.isLoading ? (
              <p className="text-sm text-muted-foreground">{t("printerOps.files.loading")}</p>
            ) : files.isError ? (
              <p role="alert" className="text-sm text-destructive">{t("printerOps.files.error")}</p>
            ) : entries.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("printerOps.files.empty")}</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("printerOps.files.name")}</TableHead>
                    <TableHead>{t("printerOps.files.size")}</TableHead>
                    <TableHead>{t("printerOps.files.modified")}</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {entries.map((f) => (
                    <TableRow key={f.path} data-state={selected?.path === f.path ? "selected" : undefined}>
                      <TableCell>
                        {f.is_dir ? (
                          <button type="button" className="flex items-center gap-1.5 hover:underline" onClick={() => goTo(f.path)}>
                            <FolderIcon className="size-4" />
                            {f.name}
                          </button>
                        ) : (
                          <button type="button" className="flex items-center gap-1.5 text-left" onClick={() => setSelected(f)}>
                            <FileIcon className="size-4" />
                            {f.name}
                          </button>
                        )}
                      </TableCell>
                      <TableCell className="tabular-nums">{f.is_dir ? "" : fmtSize(f.size)}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{f.mtime ? new Date(f.mtime).toLocaleString() : ""}</TableCell>
                      <TableCell className="text-right">
                        {!f.is_dir ? (
                          <div className="flex justify-end gap-1">
                            {canControl && sdPrint && isPrintable(f.name) ? (
                              <Button type="button" variant="outline" size="xs" onClick={() => setToPrint(f)}>
                                <PlayIcon />
                                {t("printerOps.files.print")}
                              </Button>
                            ) : null}
                            <Button asChild variant="ghost" size="icon-xs">
                              <a href={fileDownloadUrl(printerId, f.path)} download aria-label={`${t("printerOps.files.download")} ${f.name}`}>
                                <DownloadIcon />
                              </a>
                            </Button>
                            {canControl ? (
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon-xs"
                                aria-label={`${t("printerOps.files.delete")} ${f.name}`}
                                onClick={() => setToDelete(f)}
                              >
                                <Trash2Icon />
                              </Button>
                            ) : null}
                          </div>
                        ) : null}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
          {previewSlot && selected ? <div className="w-64 shrink-0">{previewSlot(selected)}</div> : null}
        </div>
        {toDelete ? (
          <ConfirmDialog
            open
            onOpenChange={(o) => !o && setToDelete(null)}
            title={t("printerOps.files.deleteTitle", { name: toDelete.name })}
            description={t("printerOps.files.deleteDesc")}
            confirmLabel={t("printerOps.files.delete")}
            destructive
            onConfirm={() => {
              const f = toDelete;
              void del.mutateAsync(f.path).then(
                () => {
                  toast.success(t("printerOps.files.deleted"));
                  if (selected?.path === f.path) setSelected(null);
                },
                () => undefined,
              );
            }}
          />
        ) : null}
        {toPrint ? (
          <ConfirmDialog
            open
            onOpenChange={(o) => !o && setToPrint(null)}
            title={t("printerOps.files.printTitle", { name: toPrint.name })}
            description={t("printerOps.files.printDesc")}
            confirmLabel={t("printerOps.files.print")}
            onConfirm={() => {
              const f = toPrint;
              void print.mutateAsync({ path: f.path }).then(
                () => {
                  toast.success(t("printerOps.files.printStarted"));
                  onOpenChange(false);
                },
                () => undefined,
              );
            }}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
