/** WiFi/Ethernet, HMS, queue, firmware, door and dev-mode chips. `extra` is the `badge-extra` slot. */
import { DoorOpenIcon, ShieldAlertIcon, WifiIcon, CableIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { PrinterOut, PrinterStatusOut } from "@/api/types";
import { Badge } from "@/components/ui/badge";

import { capsOf } from "./caps";

export function BadgeRow({
  printer,
  status,
  queueCount,
  onHms,
  extra,
}: {
  printer: PrinterOut;
  status: PrinterStatusOut;
  queueCount: number;
  onHms?: () => void;
  extra?: ReactNode;
}) {
  const { t } = useTranslation();
  const caps = capsOf(printer);
  const hms = status.hms?.length ?? 0;
  const fw = status.firmware?.ota;
  return (
    <div className="pc-t11 flex flex-wrap items-center gap-1.5 empty:hidden" data-slot="badge-row">
      {status.online ? (
        <Badge variant="outline" title={t("card.badge.network")}>
          {status.wifi_signal != null ? <WifiIcon /> : <CableIcon />}
          {status.wifi_signal != null ? `${status.wifi_signal}dBm` : t("card.badge.ethernet")}
        </Badge>
      ) : null}
      {hms > 0 ? (
        onHms ? (
          <button type="button" onClick={onHms} aria-label={t("card.badge.hms", { n: hms })}>
            <Badge variant="destructive">
              <ShieldAlertIcon />
              {t("card.badge.hms", { n: hms })}
            </Badge>
          </button>
        ) : (
          <Badge variant="destructive">
            <ShieldAlertIcon />
            {t("card.badge.hms", { n: hms })}
          </Badge>
        )
      ) : null}
      {queueCount > 0 ? <Badge variant="secondary">{t("card.badge.queued", { n: queueCount })}</Badge> : null}
      {fw ? <Badge variant="outline">{t("card.badge.fw", { v: fw })}</Badge> : null}
      {caps.door ? (
        <Badge
          variant={status.door_open ? "secondary" : "outline"}
          title={t("card.badge.doorTitle")}
          data-testid="door-chip"
        >
          <DoorOpenIcon />
          {status.door_open == null
            ? t("card.badge.door")
            : status.door_open
              ? t("card.badge.doorOpen")
              : t("card.badge.doorClosed")}
        </Badge>
      ) : null}
      {status.dev_mode === false ? (
        <Badge variant="destructive" title={status.dev_mode_reason ?? t("card.badge.devModeTip")}>
          {t("card.badge.devModeOff")}
        </Badge>
      ) : null}
      {extra}
    </div>
  );
}
