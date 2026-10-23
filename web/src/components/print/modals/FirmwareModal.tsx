import { ApiError } from "@/api/client";
import { useFirmware, useFirmwareUpload } from "@/api/printerOps";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

export function FirmwareModal({
  printerId,
  open,
  onOpenChange,
}: {
  printerId: number;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const fw = useFirmware(printerId, open);
  const upload = useFirmwareUpload(printerId);
  const d = fw.data;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Firmware</DialogTitle>
        </DialogHeader>
        {fw.isLoading || !d ? (
          <p className="text-sm text-muted-foreground">Loading...</p>
        ) : !d.model_supported ? (
          <p className="text-sm text-muted-foreground">
            Firmware lookup is not available for this model.
          </p>
        ) : (
          <div className="space-y-3 text-sm">
            <dl className="grid grid-cols-2 gap-1">
              <dt className="text-muted-foreground">Installed</dt>
              <dd>{d.current ?? "unknown"}</dd>
              <dt className="text-muted-foreground">Latest</dt>
              <dd>{d.latest ?? "unknown"}</dd>
            </dl>
            <p>{d.update_available ? "An update is available." : "Up to date."}</p>
            {d.release_notes ? (
              <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded-md bg-muted p-2 text-xs">
                {d.release_notes}
              </pre>
            ) : null}
            {d.upload?.status ? (
              <p className="text-xs text-muted-foreground">Last stage: {d.upload.status}</p>
            ) : null}
            {d.update_available ? (
              <>
                <Button
                  disabled={upload.isPending || !d.download_available}
                  onClick={() => upload.mutate()}
                >
                  {upload.isPending ? "Staging..." : "Stage to SD card"}
                </Button>
                <p className="text-xs text-muted-foreground">
                  This copies the firmware to the printer&apos;s SD card only. Confirm and start the
                  flash on the printer&apos;s own screen.
                </p>
              </>
            ) : null}
            {upload.isSuccess ? (
              <p className="text-sm text-emerald-600 dark:text-emerald-400">
                Staging started. Confirm the update on the printer screen.
              </p>
            ) : null}
            {upload.isError ? (
              <p role="alert" className="text-sm text-destructive">
                {upload.error instanceof ApiError && upload.error.status === 409
                  ? "No firmware download is available to stage right now."
                  : upload.error instanceof ApiError
                    ? upload.error.detail
                    : "Could not stage the firmware."}
              </p>
            ) : null}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
