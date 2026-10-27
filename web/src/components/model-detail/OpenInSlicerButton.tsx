/**
 * "Open in slicer" split button (R10-C, plan item 11): posts
 * `POST /files/{id}/slicer-link` for a signed, short-lived download URL and
 * hands it to a desktop slicer via its `<scheme>://open?file=<url>` deep
 * link -- the slicer fetches the URL itself, with no session cookie, so the
 * link has to carry its own (signed, expiring) auth (`app.services.signed_urls`
 * on the backend).
 *
 * Only offered for files whose format a desktop slicer actually opens raw
 * geometry from (stl/3mf/step/obj) -- not sliced outputs (gcode/gcode_3mf),
 * which already have their own "Print" action (`SendToPrinterButton`).
 *
 * Remembers the last-picked slicer in localStorage and promotes it to the
 * button's primary (non-dropdown) action next time.
 */
import { useState } from "react";
import { ChevronDownIcon, ExternalLinkIcon, ScissorsIcon } from "lucide-react";
import { toast } from "sonner";

import { api, ApiError } from "@/api/client";
import type { FileOut } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  readLastSlicer,
  rememberLastSlicer,
  SLICER_OPTIONS,
  type SlicerOption,
} from "@/lib/slicers";

interface SlicerLinkResponse {
  url: string;
  expires_at: string;
}

export function OpenInSlicerButton({
  file,
  size = "icon-sm",
  onSlice,
}: {
  file: FileOut;
  size?: "icon-sm" | "default";
  /** When set (header only), the primary segment becomes "Slice..." and the
   * slicers move into the dropdown as "Open in <slicer>" items -- one split
   * button for both actions. */
  onSlice?: () => void;
}) {
  const [lastSlicer, setLastSlicer] = useState<SlicerOption>(readLastSlicer);
  const [pending, setPending] = useState(false);

  async function openIn(slicer: SlicerOption) {
    setLastSlicer(slicer);
    rememberLastSlicer(slicer.id);
    setPending(true);
    try {
      const { url } = await api.post<SlicerLinkResponse>(`/files/${file.id}/slicer-link`);
      toast(`Opening in ${slicer.label}…`, {
        description: `If nothing happens, install ${slicer.label} or check its URL-handler setting.`,
      });
      window.location.assign(`${slicer.scheme}://open?file=${encodeURIComponent(url)}`);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.detail : `Could not open ${file.rel_path} in a slicer`);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="inline-flex">
      <Button
        type="button"
        variant="outline"
        size={size}
        className="rounded-r-none"
        disabled={pending}
        aria-label={onSlice ? "Slice" : `Open ${file.rel_path} in ${lastSlicer.label}`}
        onClick={() => (onSlice ? onSlice() : void openIn(lastSlicer))}
      >
        {onSlice ? (
          <>
            <ScissorsIcon className="size-4" />
            Slice…
          </>
        ) : size === "icon-sm" ? (
          <ExternalLinkIcon className="size-4" />
        ) : (
          `Open in ${lastSlicer.label}`
        )}
      </Button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size={size}
            className="rounded-l-none border-l-0 px-1.5"
            disabled={pending}
            aria-label="Choose slicer"
          >
            <ChevronDownIcon className="size-3.5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {SLICER_OPTIONS.map((slicer) => (
            <DropdownMenuItem key={slicer.id} onSelect={() => void openIn(slicer)}>
              {onSlice ? `Open in ${slicer.label}` : slicer.label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
