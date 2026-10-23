import { useState, type FormEvent } from "react";
import { ArrowDown, ArrowUp, Trash2 } from "lucide-react";

import { ApiError } from "@/api/client";
import {
  useCreateExternalLink,
  useDeleteExternalLink,
  useExternalLinks,
  useUpdateExternalLink,
  type ExternalLink,
} from "@/api/externalLinks";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function ExternalLinksCard() {
  const links = useExternalLinks();
  const create = useCreateExternalLink();
  const update = useUpdateExternalLink();
  const remove = useDeleteExternalLink();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [openMode, setOpenMode] = useState<ExternalLink["open_mode"]>("iframe");

  const items = links.data ?? [];
  const error = [create, update, remove].find((m) => m.isError)?.error;

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate(
      {
        name: name.trim(),
        url: url.trim(),
        icon: null,
        open_mode: openMode,
        sort_order: items.length ? Math.max(...items.map((l) => l.sort_order)) + 1 : 0,
      },
      {
        onSuccess: () => {
          setName("");
          setUrl("");
        },
      },
    );
  }

  // Swap sort_order with the neighbour; equal values are first de-duplicated
  // by using the list index as the order.
  function move(index: number, delta: -1 | 1) {
    const other = items[index + delta];
    if (!other) return;
    update.mutate({ id: items[index].id, sort_order: index + delta });
    update.mutate({ id: other.id, sort_order: index });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sidebar links</CardTitle>
        <CardDescription>
          Add links to other tools (Grafana, Home Assistant, …) under &ldquo;Links&rdquo; in the sidebar. Embedding
          only works for sites that allow being framed; otherwise use &ldquo;New tab&rdquo;.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {items.length === 0 && !links.isLoading && <p className="text-sm text-muted-foreground">No links yet.</p>}
        <ul className="space-y-1.5">
          {items.map((link, index) => (
            <li key={link.id} className="flex items-center gap-2 text-sm">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{link.name}</div>
                <div className="truncate text-xs text-muted-foreground">
                  {link.url} · {link.open_mode === "iframe" ? "embedded" : "new tab"}
                </div>
              </div>
              <Button type="button" variant="ghost" size="icon-sm" aria-label={`Move ${link.name} up`} disabled={index === 0} onClick={() => move(index, -1)}>
                <ArrowUp className="size-4" />
              </Button>
              <Button type="button" variant="ghost" size="icon-sm" aria-label={`Move ${link.name} down`} disabled={index === items.length - 1} onClick={() => move(index, 1)}>
                <ArrowDown className="size-4" />
              </Button>
              <Button type="button" variant="ghost" size="icon-sm" aria-label={`Delete ${link.name}`} onClick={() => remove.mutate(link.id)}>
                <Trash2 className="size-4" />
              </Button>
            </li>
          ))}
        </ul>
        <form onSubmit={submit} className="grid gap-3 sm:grid-cols-[1fr_2fr_auto_auto] sm:items-end">
          <div className="space-y-1.5">
            <Label htmlFor="ext-name">Name</Label>
            <Input id="ext-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ext-url">URL</Label>
            <Input id="ext-url" type="url" placeholder="https://" value={url} onChange={(e) => setUrl(e.target.value)} required />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ext-mode">Open in</Label>
            <select
              id="ext-mode"
              value={openMode}
              onChange={(e) => setOpenMode(e.target.value as ExternalLink["open_mode"])}
              className="h-9 rounded-md border border-input bg-background px-2 text-sm"
            >
              <option value="iframe">Embedded</option>
              <option value="newtab">New tab</option>
            </select>
          </div>
          <Button type="submit" disabled={create.isPending}>
            Add link
          </Button>
        </form>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error instanceof ApiError ? error.detail : "Could not save the link."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
