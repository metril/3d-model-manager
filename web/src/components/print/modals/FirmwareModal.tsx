import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useFirmware, useFirmwareUpload } from "@/api/printerOps";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useTranslation } from "react-i18next";

export function FirmwareModal({
  printerId,
  open,
  onOpenChange,
}: {
  printerId: number;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const { t } = useTranslation();
  const { can, canPrinter } = useAuth();
  const canStage = can("settings:write") && canPrinter(printerId, "control");
  const fw = useFirmware(printerId, open);
  const upload = useFirmwareUpload(printerId);
  const d = fw.data;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("firmware.title")}</DialogTitle>
        </DialogHeader>
        {fw.isLoading || !d ? (
          <p className="text-sm text-muted-foreground">{t("firmware.loading")}</p>
        ) : !d.model_supported ? (
          <p className="text-sm text-muted-foreground">
            {t("firmware.unavailable")}
          </p>
        ) : (
          <div className="space-y-3 text-sm">
            <dl className="grid grid-cols-2 gap-1">
              <dt className="text-muted-foreground">{t("firmware.installed")}</dt>
              <dd>{d.current ?? t("firmware.unknown")}</dd>
              <dt className="text-muted-foreground">{t("firmware.latest")}</dt>
              <dd>{d.latest ?? t("firmware.unknown")}</dd>
            </dl>
            <p>{d.update_available ? t("firmware.updateAvailable") : t("firmware.upToDate")}</p>
            {d.release_notes ? (
              <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded-md bg-muted p-2 text-xs">
                {d.release_notes}
              </pre>
            ) : null}
            {d.upload?.status ? (
              <p className="text-xs text-muted-foreground">{t("firmware.lastStage", { status: d.upload.status })}</p>
            ) : null}
            {d.update_available && canStage ? (
              <>
                <Button
                  disabled={upload.isPending || !d.download_available}
                  onClick={() => upload.mutate()}
                >
                  {upload.isPending ? t("firmware.staging") : t("firmware.stage")}
                </Button>
                <p className="text-xs text-muted-foreground">
                  {t("firmware.stageNote")}
                </p>
              </>
            ) : null}
            {upload.isSuccess ? (
              <p className="text-sm text-success">
                {t("firmware.stagingStarted")}
              </p>
            ) : null}
            {upload.isError ? (
              <p role="alert" className="text-sm text-destructive">
                {upload.error instanceof ApiError && upload.error.status === 409
                  ? t("firmware.noDownload")
                  : upload.error instanceof ApiError
                    ? upload.error.detail
                    : t("firmware.stageFailed")}
              </p>
            ) : null}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
