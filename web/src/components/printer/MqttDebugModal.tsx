import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { fetchMqttHistory, mqttStreamUrl, type MqttDebugEntry } from "@/api/adminLogs";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { withSeq, type MqttRow } from "@/lib/mqttRows";
import { matchesFilter } from "@/lib/mqttFilter";
import { cn } from "@/lib/utils";

const MAX_ROWS = 500;

/** Admin: live raw MQTT report stream of one printer (from printerd via Redis/SSE). */
export function MqttDebugModal({
  printerId,
  printerName,
  open,
  onOpenChange,
}: {
  printerId: number;
  printerName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<MqttRow[]>([]);
  const [paused, setPaused] = useState(false);
  const [filter, setFilter] = useState("");
  const [onlyCmd, setOnlyCmd] = useState(false);
  const [openKey, setOpenKey] = useState<number | null>(null);
  const pausedRef = useRef(false);
  pausedRef.current = paused;
  const seqRef = useRef(0);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    let raf = 0;
    let pending: MqttDebugEntry[] = [];
    setRows([]);
    void fetchMqttHistory(printerId, 50)
      .then((h) => {
        if (!alive) return;
        const tagged = withSeq(h, seqRef.current);
        seqRef.current += tagged.length;
        setRows((cur) => [...tagged, ...cur].slice(-MAX_ROWS));
      })
      .catch(() => undefined);
    const flush = () => {
      raf = 0;
      const batch = withSeq(pending, seqRef.current);
      seqRef.current += batch.length;
      pending = [];
      if (batch.length) setRows((cur) => [...cur, ...batch].slice(-MAX_ROWS));
    };
    const es = new EventSource(mqttStreamUrl(printerId));
    es.onmessage = (ev) => {
      if (pausedRef.current) return;
      try {
        pending.push(JSON.parse(ev.data) as MqttDebugEntry);
        if (!raf) raf = requestAnimationFrame(flush);
      } catch {
        /* ignore malformed frame */
      }
    };
    return () => {
      alive = false;
      if (raf) cancelAnimationFrame(raf);
      es.close();
    };
  }, [open, printerId]);

  const shown = useMemo(
    () => rows.filter((r) => (!onlyCmd || r.dir === "cmd") && matchesFilter(r, filter)).slice(-200).reverse(),
    [rows, filter, onlyCmd],
  );

  const copy = (value: unknown) => {
    void navigator.clipboard
      ?.writeText(JSON.stringify(value, null, 2))
      .then(() => toast.success(t("printer.mqttDebug.copied")))
      .catch(() => toast.error(t("printer.mqttDebug.copyFailed")));
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t("printer.mqttDebug.title", { name: printerName })}</DialogTitle>
          <DialogDescription>{t("printer.mqttDebug.desc")}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            aria-label={t("printer.mqttDebug.filter")}
            placeholder={t("printer.mqttDebug.filter")}
            className="h-9 w-56"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <label className="flex items-center gap-1.5 text-sm">
            <input type="checkbox" checked={onlyCmd} onChange={(e) => setOnlyCmd(e.target.checked)} />
            {t("printer.mqttDebug.onlyCommands")}
          </label>
          <Button type="button" variant="outline" size="sm" onClick={() => setPaused((p) => !p)}>
            {paused ? t("printer.mqttDebug.resume") : t("printer.mqttDebug.pause")}
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={() => setRows([])}>
            {t("printer.mqttDebug.clear")}
          </Button>
          <Button type="button" variant="outline" size="sm" disabled={!shown.length} onClick={() => copy(shown.map((r) => r.payload))}>
            {t("printer.mqttDebug.copyAll")}
          </Button>
          <span className="ml-auto text-xs text-muted-foreground">
            {paused ? t("printer.mqttDebug.paused") : t("printer.mqttDebug.live")} - {shown.length}/{rows.length}
          </span>
        </div>
        <div data-testid="mqtt-rows" className="max-h-[55vh] space-y-1 overflow-auto rounded-md border bg-muted/30 p-2 font-mono text-xs">
          {shown.length === 0 ? (
            <p className="text-muted-foreground">{t("printer.mqttDebug.empty")}</p>
          ) : (
            shown.map((r) => (
              <div key={r.seq} className="rounded border bg-background/60 p-1.5">
                <div className="flex items-center gap-2">
                  <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={() => setOpenKey(openKey === r.seq ? null : r.seq)}>
                    <span className="text-muted-foreground">{new Date(r.ts * 1000).toLocaleTimeString()}</span>
                    <span className={cn("rounded px-1", r.dir === "cmd" ? "bg-warning/20" : "bg-primary/20")}>
                      {r.dir === "cmd" ? t("printer.mqttDebug.cmd") : t("printer.mqttDebug.in")}
                    </span>
                    <span className="truncate">{r.topic}</span>
                  </button>
                  <Button type="button" variant="ghost" size="sm" className="h-6 px-2" onClick={() => copy(r.payload)}>
                    {t("printer.mqttDebug.copy")}
                  </Button>
                </div>
                {openKey === r.seq ? <pre className="mt-1 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(r.payload, null, 2)}</pre> : null}
              </div>
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
