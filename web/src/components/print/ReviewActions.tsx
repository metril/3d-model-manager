/**
 * Approve / Reject (with an optional note popover) for a pending queue
 * entry (P7 review/billing plan Task 9). Shared by `ReviewInboxCard` and
 * `QueuePage`'s pending rows.
 */
import { useState } from "react";

import { useReviewEntry } from "@/api/queue";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";
import { useTranslation } from "react-i18next";

export function ReviewActions({ entryId }: { entryId: number }) {
  const { t } = useTranslation();
  const review = useReviewEntry();
  const [note, setNote] = useState("");
  const [rejectOpen, setRejectOpen] = useState(false);

  function approve() {
    review.mutate({ entryId, body: { action: "approve" } });
  }

  function reject() {
    review.mutate(
      { entryId, body: { action: "reject", note: note.trim() || null } },
      { onSuccess: () => setRejectOpen(false) },
    );
  }

  return (
    <div className="flex items-center gap-1.5">
      <Button type="button" size="sm" disabled={review.isPending} onClick={approve}>
        {t("reviewActions.approve")}
      </Button>
      <Popover open={rejectOpen} onOpenChange={setRejectOpen}>
        <PopoverTrigger asChild>
          <Button type="button" size="sm" variant="outline" disabled={review.isPending}>
            {t("reviewActions.reject")}
          </Button>
        </PopoverTrigger>
        <PopoverContent>
          <div className="space-y-2">
            <Textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder={t("reviewActions.reason")}
              aria-label={t("reviewActions.noteAria")}
            />
            <Button type="button" size="sm" variant="destructive" disabled={review.isPending} onClick={reject}>
              {t("reviewActions.confirmReject")}
            </Button>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
