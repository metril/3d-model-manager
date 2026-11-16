import { Link } from "@tanstack/react-router";
import { ExternalLinkIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

/** Icon link to the full single-printer page (`/printers/$id`). */
export function OpenPrinterLink({ id, name }: { id: number; name: string }) {
  const { t } = useTranslation();
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Link
            to="/printers/$id"
            params={{ id: String(id) }}
            aria-label={t("card.open", { name })}
            className="inline-flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <ExternalLinkIcon className="size-3.5" />
          </Link>
        </TooltipTrigger>
        <TooltipContent>{t("card.openTip")}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
