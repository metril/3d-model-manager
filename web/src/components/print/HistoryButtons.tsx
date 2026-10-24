import { useState } from "react";
import { useTranslation } from "react-i18next";

import { AmsHistoryModal } from "@/components/print/modals/AmsHistoryModal";
import { HeaterHistoryModal } from "@/components/print/modals/HeaterHistoryModal";
import { Button } from "@/components/ui/button";

/** Temperature history always, AMS history only when the printer reports units. */
export function HistoryButtons({ printerId, amsUnits }: { printerId: number; amsUnits: number[] }) {
  const { t } = useTranslation();
  const [modal, setModal] = useState<"heater" | "ams" | null>(null);
  return (
    <div className="flex gap-2">
      <Button type="button" size="sm" variant="outline" onClick={() => setModal("heater")}>
        {t("history.heaterBtn")}
      </Button>
      {amsUnits.length > 0 && (
        <Button type="button" size="sm" variant="outline" onClick={() => setModal("ams")}>
          {t("history.amsBtn")}
        </Button>
      )}
      <HeaterHistoryModal printerId={printerId} open={modal === "heater"} onOpenChange={(o) => !o && setModal(null)} />
      <AmsHistoryModal printerId={printerId} units={amsUnits} open={modal === "ams"} onOpenChange={(o) => !o && setModal(null)} />
    </div>
  );
}
