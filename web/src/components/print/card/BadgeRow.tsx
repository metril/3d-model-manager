/** Connection, WiFi/Ethernet, HMS, queue, firmware, door and dev-mode chips. `extra` is the `badge-extra` slot. */
import { DoorOpenIcon, ShieldAlertIcon, WifiIcon, CableIcon } from "lucide-react";
import type { ReactNode } from "react";

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
  const caps = capsOf(printer);
  const hms = status.hms?.length ?? 0;
  const fw = status.firmware?.ota;
  return (
    <div className="pc-t11 flex flex-wrap items-center gap-1.5" data-slot="badge-row">
      <Badge variant={status.online ? "secondary" : "outline"}>{status.online ? "Connected" : "Offline"}</Badge>
      {status.online ? (
        <Badge variant="outline" title="Network">
          {status.wifi_signal ? <WifiIcon /> : <CableIcon />}
          {status.wifi_signal ?? "Ethernet"}
        </Badge>
      ) : null}
      {hms > 0 ? (
        onHms ? (
          <button type="button" onClick={onHms} aria-label={`HMS ${hms}`}>
            <Badge variant="destructive">
              <ShieldAlertIcon />
              HMS {hms}
            </Badge>
          </button>
        ) : (
          <Badge variant="destructive">
            <ShieldAlertIcon />
            HMS {hms}
          </Badge>
        )
      ) : null}
      {queueCount > 0 ? <Badge variant="secondary">{queueCount} queued</Badge> : null}
      {fw ? <Badge variant="outline">FW {fw}</Badge> : null}
      {caps.door ? (
        <Badge variant="outline" title="Door sensor present">
          <DoorOpenIcon />
          Door
        </Badge>
      ) : null}
      {status.dev_mode === false ? (
        <Badge variant="destructive" title={status.dev_mode_reason ?? "Developer LAN mode is off"}>
          Dev Mode off
        </Badge>
      ) : null}
      {extra}
    </div>
  );
}
