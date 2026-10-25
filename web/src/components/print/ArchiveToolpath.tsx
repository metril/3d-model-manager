/** Collapsible G-code toolpath for an archived run; nothing loads until opened. */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { archiveGcodeUrl, useArchiveGcodePlates } from "@/api/archives";
import { LazyGcodeToolpathViewer } from "@/components/gcode";
import { Button } from "@/components/ui/button";

export function ArchiveToolpath({ archiveId, archivePlate }: { archiveId: number; archivePlate?: number | null }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [plate, setPlate] = useState<number | null>(null);
  const plates = useArchiveGcodePlates(archiveId, open);
  const list = plates.data ?? [];
  const current = plate ?? list.find((p) => p.plate === archivePlate)?.plate ?? list[0]?.plate;

  return (
    <section className="space-y-2" data-testid="archive-toolpath">
      <Button type="button" variant="outline" size="sm" onClick={() => setOpen((o) => !o)}>
        {open ? t("archiveX.toolpath.hide") : t("archiveX.toolpath.show")}
      </Button>
      {open && !plates.isPending ? (
        list.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("archiveX.toolpath.empty")}</p>
        ) : (
          <>
            {list.length > 1 ? (
              <label className="flex items-center gap-2 text-sm">
                {t("archiveX.toolpath.plate")}
                <select
                  className="rounded-md border bg-background px-2 py-1"
                  value={current}
                  onChange={(e) => setPlate(Number(e.target.value))}
                >
                  {list.map((p) => (
                    <option key={p.plate} value={p.plate}>
                      {p.plate}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <LazyGcodeToolpathViewer
              key={current}
              gcodeUrl={archiveGcodeUrl(archiveId, current)}
              className="h-72 w-full"
            />
          </>
        )
      ) : null}
    </section>
  );
}
