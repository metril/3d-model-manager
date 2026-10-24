/**
 * Global Developer-Mode banner (Q3). Red/amber variant for printers whose
 * Developer LAN mode is known to be off (`GET /printers/developer-mode-warnings`,
 * 60 s poll); grey "unknown" variant for online printers still reporting
 * `dev_mode === null`. Dismissible per browser session (re-shows if the set of
 * affected printers changes).
 */
import { AlertTriangleIcon, HelpCircleIcon, XIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useDevModeUnknown, useDevModeWarnings } from "@/api/printerControl";
import { useFeatures } from "@/api/features";
import { cn } from "@/lib/utils";

export const DEV_MODE_WIKI = "https://wiki.bambulab.com/en/knowledge-sharing/enable-developer-mode";
const KEY = "devModeBannerDismissed";

function readDismissed(): Set<string> {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(KEY) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

function Banner({
  variant,
  names,
  onDismiss,
}: {
  variant: "off" | "unknown";
  names: string[];
  onDismiss: () => void;
}) {
  const { t } = useTranslation();
  const off = variant === "off";
  return (
    <div
      role={off ? "alert" : "status"}
      data-testid={`dev-mode-banner-${variant}`}
      className={cn(
        "flex items-center gap-2 border-b px-6 py-2 text-sm",
        off ? "bg-amber-500/10 text-amber-900 dark:text-amber-200" : "bg-muted text-muted-foreground",
      )}
    >
      {off ? <AlertTriangleIcon className="size-4 shrink-0" /> : <HelpCircleIcon className="size-4 shrink-0" />}
      <span className="min-w-0 flex-1">
        {off
          ? t("card.devBanner.off", { names: names.join(", ") })
          : t("card.devBanner.unknown", { names: names.join(", ") })}{" "}
        <a href={DEV_MODE_WIKI} target="_blank" rel="noopener noreferrer" className="underline">
          {t("card.devBanner.how")}
        </a>
      </span>
      <button type="button" aria-label={t("card.devBanner.dismiss")} className="rounded p-1 hover:bg-foreground/10" onClick={onDismiss}>
        <XIcon className="size-4" />
      </button>
    </div>
  );
}

export function DevModeBanner() {
  const features = useFeatures();
  const enabled = !!features.data?.printer_enabled;
  const warnings = useDevModeWarnings({ enabled });
  const unknown = useDevModeUnknown({ enabled });
  const [dismissed, setDismissed] = useState(readDismissed);

  function dismiss(sig: string) {
    const next = new Set(dismissed).add(sig);
    setDismissed(next);
    try {
      sessionStorage.setItem(KEY, JSON.stringify([...next]));
    } catch {
      // storage unavailable: dismissal lasts until reload
    }
  }

  const offNames = (warnings.data ?? []).map((w) => w.name);
  const unknownNames = (unknown.data ?? []).map((u) => u.name);
  const offSig = `off:${offNames.join("|")}`;
  const unknownSig = `unknown:${unknownNames.join("|")}`;

  return (
    <>
      {offNames.length > 0 && !dismissed.has(offSig) ? (
        <Banner variant="off" names={offNames} onDismiss={() => dismiss(offSig)} />
      ) : null}
      {unknownNames.length > 0 && !dismissed.has(unknownSig) ? (
        <Banner variant="unknown" names={unknownNames} onDismiss={() => dismiss(unknownSig)} />
      ) : null}
    </>
  );
}
