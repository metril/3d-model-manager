/** `footer-extra` slot (camera split button, plug row, firmware, history) and Print -> PrintDialog. */
import { PlayIcon } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { PrintDialog } from "@/components/print/PrintDialog";
import { Button } from "@/components/ui/button";

export function CardFooter({
  printerId,
  canPrint,
  online,
  extra,
}: {
  printerId?: number;
  canPrint: boolean;
  online: boolean;
  /** `footer-extra` slot: Q4 `CameraSplitButton`, Q7 `PlugRow`. */
  extra?: ReactNode;
}) {
  const { t } = useTranslation();
  const [printOpen, setPrintOpen] = useState(false);
  return (
    <div className="flex flex-wrap items-center gap-2" data-slot="card-footer">
      {extra}
      {canPrint ? (
        <Button type="button" size="sm" className="ml-auto" disabled={!online} onClick={() => setPrintOpen(true)}>
          <PlayIcon />
          {t("card.footer.print")}
        </Button>
      ) : null}
      {printOpen ? <PrintDialog open onOpenChange={setPrintOpen} source={{ kind: "pick" }} printerId={printerId} /> : null}
    </div>
  );
}
