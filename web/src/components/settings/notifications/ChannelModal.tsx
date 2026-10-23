/** Add/edit a notification channel. Secrets come back as "********" and are kept server-side if echoed. */
import { useState } from "react";

import { ApiError } from "@/api/client";
import {
  useCreateChannel,
  useUpdateChannel,
  type NotificationCatalog,
  type NotificationChannel,
} from "@/api/notifications";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const SELECT_CLASS = "h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs disabled:opacity-50";

interface Props {
  catalog: NotificationCatalog;
  channel?: NotificationChannel;
  onClose: () => void;
}

export function ChannelModal({ catalog, channel, onClose }: Props) {
  const create = useCreateChannel();
  const update = useUpdateChannel();
  const [kind, setKind] = useState(channel?.kind ?? catalog.kinds[0]?.kind ?? "");
  const [name, setName] = useState(channel?.name ?? "");
  const [config, setConfig] = useState<Record<string, string>>(channel?.config ?? {});
  const [events, setEvents] = useState<string[]>(channel?.events ?? []);
  const [quiet, setQuiet] = useState(!!channel?.quiet_hours);
  const [qStart, setQStart] = useState(channel?.quiet_hours?.start ?? "22:00");
  const [qEnd, setQEnd] = useState(channel?.quiet_hours?.end ?? "07:00");
  const [qTz, setQTz] = useState(channel?.quiet_hours?.tz ?? "UTC");
  const [digest, setDigest] = useState(channel?.digest_cron ?? "");
  const [photo, setPhoto] = useState(channel?.attach_photo ?? false);

  const kindDef = catalog.kinds.find((k) => k.kind === kind);
  const pending = create.isPending || update.isPending;
  const error = create.error ?? update.error;
  const missing = (kindDef?.fields ?? []).some(
    (f) => f.required && !(config[f.name] ?? f.default ?? "").toString().trim(),
  );

  function changeKind(k: string) {
    setKind(k);
    setConfig({});
  }
  function toggleEvent(id: string) {
    setEvents((cur) => (cur.includes(id) ? cur.filter((e) => e !== id) : [...cur, id]));
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!kindDef) return;
    const cfg: Record<string, string> = {};
    for (const f of kindDef.fields) {
      const v = config[f.name] ?? f.default ?? "";
      if (v !== "") cfg[f.name] = v;
    }
    const body = {
      name: name.trim(),
      config: cfg,
      events,
      quiet_hours: quiet ? { start: qStart, end: qEnd, tz: qTz.trim() || "UTC" } : null,
      digest_cron: digest.trim() || null,
      attach_photo: kindDef.supports_photo ? photo : false,
    };
    const opts = { onSuccess: onClose };
    if (channel) update.mutate({ id: channel.id, body }, opts);
    else create.mutate({ kind, ...body }, opts);
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{channel ? "Edit channel" : "Add channel"}</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="nc-kind">Type</Label>
            <select
              id="nc-kind"
              className={SELECT_CLASS}
              value={kind}
              disabled={!!channel}
              onChange={(e) => changeKind(e.target.value)}
            >
              {catalog.kinds.map((k) => (
                <option key={k.kind} value={k.kind}>
                  {k.label}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="nc-name">Name</Label>
            <Input id="nc-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={64} />
          </div>
          {(kindDef?.fields ?? []).map((f) => {
            const id = `nc-f-${f.name}`;
            const value = config[f.name] ?? f.default ?? "";
            return (
              <div key={f.name} className="space-y-1">
                <Label htmlFor={id}>{f.label}</Label>
                {f.type === "select" ? (
                  <select
                    id={id}
                    className={SELECT_CLASS}
                    value={value}
                    onChange={(e) => setConfig({ ...config, [f.name]: e.target.value })}
                  >
                    {(f.options ?? []).map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                  </select>
                ) : (
                  <Input
                    id={id}
                    type={f.type === "password" || f.secret ? "password" : f.type}
                    value={value}
                    placeholder={f.placeholder ?? undefined}
                    required={f.required}
                    autoComplete="off"
                    onChange={(e) => setConfig({ ...config, [f.name]: e.target.value })}
                  />
                )}
              </div>
            );
          })}
          <fieldset className="space-y-1">
            <legend className="text-sm font-medium">Events</legend>
            {catalog.events.map((ev) => (
              <label key={ev.id} className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  aria-label={ev.label}
                  checked={events.includes(ev.id)}
                  onChange={() => toggleEvent(ev.id)}
                />
                <span>
                  {ev.label}
                  <span className="block text-xs text-muted-foreground">{ev.description}</span>
                </span>
              </label>
            ))}
          </fieldset>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={quiet} onChange={(e) => setQuiet(e.target.checked)} />
            Quiet hours
          </label>
          {quiet && (
            <div className="grid grid-cols-3 gap-2">
              <div className="space-y-1">
                <Label htmlFor="nc-qs">Start</Label>
                <Input id="nc-qs" type="time" value={qStart} onChange={(e) => setQStart(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="nc-qe">End</Label>
                <Input id="nc-qe" type="time" value={qEnd} onChange={(e) => setQEnd(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="nc-qz">Timezone</Label>
                <Input id="nc-qz" value={qTz} onChange={(e) => setQTz(e.target.value)} placeholder="UTC" />
              </div>
            </div>
          )}
          <div className="space-y-1">
            <Label htmlFor="nc-digest">Digest cron</Label>
            <Input
              id="nc-digest"
              value={digest}
              onChange={(e) => setDigest(e.target.value)}
              placeholder="e.g. 0 9 * * * (blank = send immediately)"
            />
          </div>
          {kindDef?.supports_photo && (
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={photo} onChange={(e) => setPhoto(e.target.checked)} />
              Attach camera photo
            </label>
          )}
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error instanceof ApiError ? error.detail : "Could not save the channel."}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={pending || !name.trim() || missing}>
              {channel ? "Save" : "Create channel"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
