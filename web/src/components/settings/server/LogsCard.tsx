import { DownloadIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { fetchLogs, logsDownloadUrl, type LogEntry, type LogFilter } from "@/api/adminLogs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

const SERVICES = ["api", "worker-io", "worker-cpu", "beat", "printerd", "virtual-printer"];
const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"];
const MAX_LINES = 1000;
const POLL_MS = 2000;
const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

const levelClass: Record<string, string> = {
  DEBUG: "text-muted-foreground",
  INFO: "",
  WARNING: "text-warning",
  ERROR: "text-destructive",
  CRITICAL: "font-semibold text-destructive",
};

/** Settings -> Server: tail/filter/search/download logs of every service. */
export function LogsCard() {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<LogFilter>({ service: "", level: "", q: "" });
  const [debouncedQ, setDebouncedQ] = useState("");
  const [live, setLive] = useState(true);
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [error, setError] = useState(false);
  const lastId = useRef(0);
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const id = setTimeout(() => setDebouncedQ(filter.q), 300);
    return () => clearTimeout(id);
  }, [filter.q]);

  const { service, level } = filter;
  const eff: LogFilter = { service, level, q: debouncedQ };

  const load = useCallback(
    async (reset: boolean) => {
      try {
        const out = await fetchLogs({ service, level, q: debouncedQ }, reset ? { limit: 300 } : { after: lastId.current, limit: 300 });
        setError(false);
        lastId.current = reset ? out.last_id : Math.max(lastId.current, out.last_id);
        if (reset) setEntries(out.entries);
        else if (out.entries.length) setEntries((prev) => [...prev, ...out.entries].slice(-MAX_LINES));
      } catch {
        setError(true);
      }
    },
    [service, level, debouncedQ],
  );

  useEffect(() => {
    lastId.current = 0;
    void load(true);
  }, [load]);

  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => void load(false), POLL_MS);
    return () => clearInterval(id);
  }, [live, load]);

  useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [entries]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settings.server.logs.title")}</CardTitle>
        <CardDescription>{t("settings.server.logs.desc")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <select
            aria-label={t("settings.server.logs.service")}
            className={selectClass}
            value={filter.service}
            onChange={(e) => setFilter({ ...filter, service: e.target.value })}
          >
            <option value="">{t("settings.server.logs.allServices")}</option>
            {SERVICES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <select
            aria-label={t("settings.server.logs.level")}
            className={selectClass}
            value={filter.level}
            onChange={(e) => setFilter({ ...filter, level: e.target.value })}
          >
            <option value="">{t("settings.server.logs.allLevels")}</option>
            {LEVELS.map((l) => (
              <option key={l} value={l}>
                {l}+
              </option>
            ))}
          </select>
          <Input
            aria-label={t("settings.server.logs.search")}
            placeholder={t("settings.server.logs.search")}
            className="h-9 w-56"
            value={filter.q}
            onChange={(e) => setFilter({ ...filter, q: e.target.value })}
          />
          <label className="flex items-center gap-1.5 text-sm">
            <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />
            {t("settings.server.logs.follow")}
          </label>
          <Button type="button" variant="outline" size="sm" asChild className="ml-auto">
            <a href={logsDownloadUrl(eff)} download>
              <DownloadIcon className="size-3.5" />
              {t("settings.server.logs.download")}
            </a>
          </Button>
        </div>
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {t("settings.server.logs.loadError")}
          </p>
        ) : null}
        <div
          ref={box}
          data-testid="log-lines"
          className="h-80 overflow-auto rounded-md border bg-muted/30 p-2 font-mono text-xs leading-5"
          onScroll={(e) => {
            const el = e.currentTarget;
            stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
          }}
        >
          {entries.length === 0 ? (
            <p className="text-muted-foreground">{t("settings.server.logs.empty")}</p>
          ) : (
            entries.map((e) => (
              <div key={e.id} className={cn("whitespace-pre-wrap break-all", levelClass[e.level])}>
                <span className="text-muted-foreground">
                  {new Date(e.ts * 1000).toLocaleTimeString()} [{e.service}]
                </span>{" "}
                <span className="font-semibold">{e.level}</span> {e.logger} {e.msg}
              </div>
            ))
          )}
        </div>
      </CardContent>
    </Card>
  );
}
