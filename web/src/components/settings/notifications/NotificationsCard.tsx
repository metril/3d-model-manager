/** Notification channels list (Q6): kind icon, enabled switch, edit/delete/test. */
import { Bell, Mail, Megaphone, MessageSquare, Pencil, Send, Trash2, Webhook, type LucideIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import {
  useChannels,
  useDeleteChannel,
  useNotificationCatalog,
  useNotifySettings,
  useSaveNotifySettings,
  useTestChannel,
  useUpdateChannel,
  type NotificationChannel,
} from "@/api/notifications";
import { ChannelModal } from "@/components/settings/notifications/ChannelModal";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

function kindIcon(kind: string): LucideIcon {
  if (kind === "apprise") return Megaphone;
  if (kind.includes("mail") || kind === "smtp") return Mail;
  if (kind.includes("webhook")) return Webhook;
  if (["telegram", "discord", "slack", "matrix"].includes(kind)) return MessageSquare;
  if (kind.includes("push") || kind === "ntfy" || kind === "pushover") return Send;
  return Bell;
}

function Row({ ch, kindLabel, onEdit }: { ch: NotificationChannel; kindLabel: string; onEdit: () => void }) {
  const update = useUpdateChannel();
  const del = useDeleteChannel();
  const test = useTestChannel();
  const [confirming, setConfirming] = useState(false);
  const Icon = kindIcon(ch.kind);

  function sendTest() {
    test.mutate(ch.id, {
      onSuccess: (r) =>
        r.ok ? toast.success(`Test sent to ${ch.name}`) : toast.error(r.error ?? "Test notification failed"),
      onError: () => toast.error("Test notification failed"),
    });
  }

  return (
    <li className="flex flex-wrap items-center gap-3 rounded-md border p-3">
      <Icon className="size-4 text-muted-foreground" aria-hidden />
      <div className="min-w-0">
        <div className="font-medium">{ch.name}</div>
        <div className="text-xs text-muted-foreground">
          {kindLabel} · {ch.events.length} event{ch.events.length === 1 ? "" : "s"}
        </div>
      </div>
      <div className="ml-auto flex items-center gap-2">
        <Switch
          aria-label={`Enable ${ch.name}`}
          checked={ch.enabled}
          disabled={update.isPending}
          onCheckedChange={(enabled) => update.mutate({ id: ch.id, body: { enabled } })}
        />
        <Button size="sm" variant="outline" disabled={test.isPending} onClick={sendTest} aria-label={`Test ${ch.name}`}>
          <Send className="size-4" /> Test
        </Button>
        <Button size="sm" variant="outline" onClick={onEdit} aria-label={`Edit ${ch.name}`}>
          <Pencil className="size-4" />
        </Button>
        {confirming ? (
          <Button size="sm" variant="destructive" disabled={del.isPending} onClick={() => del.mutate(ch.id)}>
            Confirm delete
          </Button>
        ) : (
          <Button size="sm" variant="outline" onClick={() => setConfirming(true)} aria-label={`Delete ${ch.name}`}>
            <Trash2 className="size-4" />
          </Button>
        )}
      </div>
    </li>
  );
}

function HmsRenotify({ clearS, afterS }: { clearS: number; afterS: number }) {
  const save = useSaveNotifySettings();
  const [clear, setClear] = useState(String(clearS));
  const [after, setAfter] = useState(String(afterS));
  const valid = [clear, after].every((v) => v !== "" && Number.isInteger(Number(v)) && Number(v) >= 0);
  return (
    <div className="space-y-2 rounded-md border p-3">
      <p className="text-sm font-medium">Fault alert repeats</p>
      <p className="text-xs text-muted-foreground">
        A printer fault notifies once per episode. It notifies again only after it stays clear for the first interval, or
        the episode lasts longer than the second.
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="hms-clear">Re-notify after clear (s)</Label>
          <Input id="hms-clear" type="number" min={0} value={clear} onChange={(e) => setClear(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="hms-after">Re-notify after episode (s)</Label>
          <Input id="hms-after" type="number" min={0} value={after} onChange={(e) => setAfter(e.target.value)} />
        </div>
        <Button
          variant="outline"
          disabled={!valid || save.isPending}
          onClick={() =>
            save.mutate(
              { hms_renotify_clear_s: Number(clear), hms_renotify_after_s: Number(after) },
              { onSuccess: () => toast.success("Saved"), onError: () => toast.error("Couldn't save") },
            )
          }
        >
          Save
        </Button>
      </div>
    </div>
  );
}

export function NotificationsCard() {
  const prefs = useNotifySettings();
  const list = useChannels();
  const catalog = useNotificationCatalog();
  const [editing, setEditing] = useState<NotificationChannel | "new" | null>(null);

  if (list.isLoading || catalog.isLoading) return <Skeleton className="h-40 w-full rounded-xl" />;
  const kinds = catalog.data?.kinds ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Channels</CardTitle>
        <CardDescription>Where print and system notifications are delivered.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {list.isError && (
          <p role="alert" className="text-sm text-destructive">
            Couldn&apos;t load notification channels.
          </p>
        )}
        {(list.data ?? []).length === 0 && <p className="text-sm text-muted-foreground">No channels yet.</p>}
        <ul className="space-y-3">
          {(list.data ?? []).map((ch) => (
            <Row
              key={ch.id}
              ch={ch}
              kindLabel={kinds.find((k) => k.kind === ch.kind)?.label ?? ch.kind}
              onEdit={() => setEditing(ch)}
            />
          ))}
        </ul>
        <Button variant="outline" onClick={() => setEditing("new")}>
          Add channel
        </Button>
        {prefs.data && (
          <HmsRenotify
            key={`${prefs.data.hms_renotify_clear_s}-${prefs.data.hms_renotify_after_s}`}
            clearS={prefs.data.hms_renotify_clear_s}
            afterS={prefs.data.hms_renotify_after_s}
          />
        )}
        {editing && catalog.data && (
          <ChannelModal
            catalog={catalog.data}
            channel={editing === "new" ? undefined : editing}
            onClose={() => setEditing(null)}
          />
        )}
      </CardContent>
    </Card>
  );
}
