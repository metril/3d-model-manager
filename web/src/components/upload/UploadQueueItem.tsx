import { RotateCcwIcon, XIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { ExistingUploadModel } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { humanizeBytes } from "@/lib/format";

export type QueueStatus = "pending" | "uploading" | "processing" | "stored" | "failed" | "duplicate";

export interface QueueItem {
  id: string;
  file: File;
  relPath: string;
  size: number;
  progress: number;
  status: QueueStatus;
  error?: string;
  jobId?: string;
  /** Set when `status === "duplicate"` (R11-C item 18): the library model
   * that already has this content, plus a suggested rename. */
  duplicate?: { existing: ExistingUploadModel; suggestedName: string };
}

const STATUS_KEY: Record<QueueStatus, string> = {
  pending: "upload.status.pending",
  uploading: "upload.status.uploading",
  processing: "upload.status.processing",
  stored: "upload.status.stored",
  failed: "upload.status.failed",
  duplicate: "upload.status.duplicate",
};

const STATUS_VARIANT: Record<QueueStatus, "outline" | "secondary" | "destructive"> = {
  pending: "outline",
  uploading: "outline",
  processing: "secondary",
  stored: "secondary",
  failed: "destructive",
  duplicate: "destructive",
};

interface UploadQueueItemProps {
  item: QueueItem;
  onRelPathChange: (relPath: string) => void;
  onRemove: () => void;
  /** Retries this item's upload with `allow_duplicate=true` (R11-C item 18). */
  onUploadAnyway?: () => void;
  /** Cancels this item's in-flight upload (it parks as failed, retryable). */
  onCancel?: () => void;
  /** Re-uploads a failed item. */
  onRetry?: () => void;
}

/** One row in the upload queue: editable rel_path, progress bar, status chip.
 * A "duplicate" item additionally shows an inline card pointing at the
 * existing model, with "Open existing"/"Upload anyway" actions. */
export function UploadQueueItem({ item, onRelPathChange, onRemove, onUploadAnyway, onCancel, onRetry }: UploadQueueItemProps) {
  const { t } = useTranslation();
  const editable = item.status === "pending";

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 rounded-lg border border-border p-3">
        <div className="min-w-0 flex-1 space-y-1.5">
          <Input
            value={item.relPath}
            onChange={(event) => onRelPathChange(event.target.value)}
            disabled={!editable}
            className="font-mono text-xs"
            aria-label={t("upload.pathFor", { name: item.file.name })}
          />
          <div className="flex items-center gap-2">
            <Progress value={item.progress} className="h-1.5 flex-1" aria-label={t("upload.progressFor", { name: item.file.name })} />
            <span className="w-14 shrink-0 text-right text-xs text-muted-foreground">{humanizeBytes(item.size)}</span>
          </div>
          {item.error ? <p className="text-xs text-destructive">{item.error}</p> : null}
        </div>
        <Badge variant={STATUS_VARIANT[item.status]}>{t(STATUS_KEY[item.status])}</Badge>
        {editable ? (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={t("upload.remove", { name: item.file.name })}
            onClick={onRemove}
          >
            <XIcon className="size-4" />
          </Button>
        ) : null}
        {item.status === "uploading" && onCancel ? (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={t("upload.cancelItem", { name: item.file.name })}
            onClick={onCancel}
          >
            <XIcon className="size-4" />
          </Button>
        ) : null}
        {item.status === "failed" && onRetry ? (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={t("upload.retryItem", { name: item.file.name })}
            onClick={onRetry}
          >
            <RotateCcwIcon className="size-4" />
          </Button>
        ) : null}
      </div>
      {item.status === "duplicate" && item.duplicate && (
        <Card className="py-3">
          <CardContent className="flex flex-wrap items-center gap-2 px-3 text-sm">
            <span>
              {t("upload.alreadyInLibrary")}{" "}
              <a href={item.duplicate.existing.url} className="font-medium underline">
                {item.duplicate.existing.name}
              </a>
            </span>
            <div className="ml-auto flex gap-2">
              <Button type="button" variant="outline" size="sm" asChild>
                <a href={item.duplicate.existing.url}>{t("upload.openExisting")}</a>
              </Button>
              <Button type="button" variant="outline" size="sm" onClick={onUploadAnyway}>
                {t("upload.uploadAnyway")}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
