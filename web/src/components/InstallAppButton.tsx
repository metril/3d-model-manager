import { Download } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { useInstallPrompt } from "@/lib/pwa";

/** Shown in the sidebar footer only while the browser offers to install. */
export function InstallAppButton() {
  const { t } = useTranslation();
  const { canInstall, install } = useInstallPrompt();
  if (!canInstall) return null;
  return (
    <Button type="button" variant="outline" size="icon" aria-label={t("installApp.install")} title={t("installApp.install")} onClick={() => void install()}>
      <Download className="size-4" />
    </Button>
  );
}
