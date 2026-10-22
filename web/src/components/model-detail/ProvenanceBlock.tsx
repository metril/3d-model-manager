import { Link } from "@tanstack/react-router";
import { ExternalLinkIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { ModelDetail } from "@/api/types";

const SITE_LABELS: Record<string, string> = {
  thingiverse: "Thingiverse", printables: "Printables", makerworld: "MakerWorld",
};

/** Attribution for an imported model (FULL line 232: provenance always shown
 * — CC attribution requires it). Renders nothing for a manually-created model. */
export function ProvenanceBlock({ model }: { model: ModelDetail }) {
  if (!model.source_url && !model.source_site && !model.source_author) return null;
  const label = model.source_site ? (SITE_LABELS[model.source_site] ?? model.source_site) : "source";
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground" data-testid="provenance">
      <span>Imported from</span>
      {model.source_url ? (
        <a href={model.source_url} target="_blank" rel="noreferrer"
           className="inline-flex items-center gap-1 font-medium text-foreground hover:underline">
          {label}<ExternalLinkIcon className="size-3.5" />
        </a>
      ) : (
        <span className="font-medium text-foreground">{label}</span>
      )}
      {model.source_author && <span>by {model.source_author}</span>}
      {model.source_license && <Badge variant="outline">{model.source_license}</Badge>}
      {model.source_collection_title && (
        <span>
          · from collection{" "}
          {model.source_collection_id != null ? (
            <Link
              to="/library"
              search={{ collection: model.source_collection_id }}
              className="font-medium text-foreground hover:underline"
            >
              {model.source_collection_title}
            </Link>
          ) : (
            <span className="font-medium text-foreground">{model.source_collection_title}</span>
          )}
        </span>
      )}
    </div>
  );
}
