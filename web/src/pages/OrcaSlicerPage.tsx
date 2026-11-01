import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import { useAuth } from "@/api/auth";
import { useFeatures } from "@/api/features";
import { useOrcaSlicerEnabled } from "@/api/orcaslicer";

/** `/slicer`: bookmark/nav entry that hands off to the Selkies stream at `/slicer/`, which
 * the browser must load directly (not in an iframe) for accurate pointer motion. */
export function OrcaSlicerPage() {
  const { t } = useTranslation();
  const enabled = useOrcaSlicerEnabled();
  const features = useFeatures();
  const auth = useAuth();
  const settled = !features.isPending && !auth.isPending;

  useEffect(() => {
    if (enabled) window.location.replace("/slicer/");
  }, [enabled]);

  if (!settled || enabled) return null;
  return <p className="p-4 text-sm text-muted-foreground">{t("orcaslicer.disabled")}</p>;
}
