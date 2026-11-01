import { useState } from "react";
import { toast } from "sonner";
import { BoxesIcon, ChevronDownIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import { isOrcaSlicerOpenable, useEndOrcaSlicerSession, useOrcaSlicerEnabled, useOrcaSlicerSession } from "@/api/orcaslicer";
import type { FileOut } from "@/api/types";
import { readOrcaSlicerMode, type OrcaSlicerOpenMode } from "@/components/orcaslicer/orcaslicerNav";
import { useOrcaSlicerLauncher } from "@/components/orcaslicer/useOrcaSlicerLauncher";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/** "Open in OrcaSlicer" split button: the main action uses the last-chosen mode (tab by
 * default); the menu picks a tab or a window explicitly (and remembers it). Renders nothing
 * unless OrcaSlicer is enabled and the file is a verified, openable format. */
export function OrcaSlicerSplitButton({
  file,
  slug,
  showLabel = false,
}: {
  file: FileOut;
  slug: string;
  showLabel?: boolean;
}) {
  const { t } = useTranslation();
  const enabled = useOrcaSlicerEnabled();
  const { launch, dialog } = useOrcaSlicerLauncher(slug, enabled);
  const session = useOrcaSlicerSession({ enabled }).data;
  const end = useEndOrcaSlicerSession();
  const [mode, setMode] = useState<OrcaSlicerOpenMode>(readOrcaSlicerMode);
  if (!enabled || !file.verified_at || !isOrcaSlicerOpenable(file)) return null;

  const open = (next: OrcaSlicerOpenMode) => {
    setMode(next);
    launch(file, next);
  };

  return (
    <div className="inline-flex">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size={showLabel ? "default" : "icon-sm"}
            className="rounded-r-none"
            aria-label={`Open ${file.rel_path} in OrcaSlicer`}
            onClick={() => open(mode)}
          >
            <BoxesIcon className="size-4" />
            {showLabel ? t("orcaslicer.open") : null}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{t("orcaslicer.open")}</TooltipContent>
      </Tooltip>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size={showLabel ? "default" : "icon-sm"}
            className="rounded-l-none border-l-0 px-1.5"
            aria-label="OrcaSlicer options"
          >
            <ChevronDownIcon className="size-3.5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onSelect={() => open("tab")}>{t("orcaslicer.tab")}</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => open("window")}>{t("orcaslicer.window")}</DropdownMenuItem>
          {session ? (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuLabel className="max-w-64 truncate font-normal">
                {t("orcaslicer.editing")} <strong>{session.file.name}</strong>
              </DropdownMenuLabel>
              <DropdownMenuItem
                disabled={end.isPending}
                onSelect={() =>
                  end.mutate(undefined, {
                    onSuccess: () => toast.success(t("orcaslicer.ended")),
                    onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("orcaslicer.endError")),
                  })
                }
              >
                {t("orcaslicer.end")}
              </DropdownMenuItem>
            </>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>
      {dialog}
    </div>
  );
}
