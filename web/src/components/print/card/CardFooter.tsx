/** Camera button (detail-page camera for now), `footer-extra` slot, and Print -> PrintDialog. */
import { Link } from "@tanstack/react-router";
import { PlayIcon, VideoIcon } from "lucide-react";
import { useState, type ReactNode } from "react";

import { PrintDialog } from "@/components/print/PrintDialog";
import { Button } from "@/components/ui/button";

export function CardFooter({
  printerId,
  canPrint,
  online,
  extra,
}: {
  printerId: number;
  canPrint: boolean;
  online: boolean;
  /** `footer-extra` slot: Q4 `CameraSplitButton`, Q7 `PlugRow`. */
  extra?: ReactNode;
}) {
  const [printOpen, setPrintOpen] = useState(false);
  return (
    <div className="flex flex-wrap items-center gap-2" data-slot="card-footer">
      {/* TODO(Q4-web): replace with <CameraSplitButton printerId onInline /> via footerExtra once merged. */}
      <Button asChild variant="outline" size="sm">
        <Link to="/printers/$id" params={{ id: String(printerId) }} aria-label="Camera">
          <VideoIcon />
          Camera
        </Link>
      </Button>
      {extra}
      {canPrint ? (
        <Button type="button" size="sm" className="ml-auto" disabled={!online} onClick={() => setPrintOpen(true)}>
          <PlayIcon />
          Print
        </Button>
      ) : null}
      {printOpen ? <PrintDialog open onOpenChange={setPrintOpen} source={{ kind: "pick" }} /> : null}
    </div>
  );
}
