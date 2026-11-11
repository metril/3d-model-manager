import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { api, ApiError } from "@/api/client";
import {
  useCreateExternalLink,
  useDeleteExternalLink,
  useExternalLinks,
  useUpdateExternalLink,
  type ExternalLink,
} from "@/api/externalLinks";
import { ConfirmDialog } from "@/components/ConfirmDialog";
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
  const [deleting, setDeleting] = useState<ExternalLink | null>(null);
  const [moving, setMoving] = useState(false);
  const queryClient = useQueryClient();
  const { t } = useTranslation();

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

  // The API has no bulk reorder, so renumber to the new list order (one PATCH
  // per link whose value changes -- two for a clean swap). The cache is
  // updated optimistically and every applied PATCH is undone on failure.
  async function move(index: number, delta: -1 | 1) {
    if (moving || !items[index + delta]) return;
    const next = [...items];
    [next[index], next[index + delta]] = [next[index + delta], next[index]];
    const changes = next.map((l, i) => ({ link: l, order: i })).filter(({ link, order }) => link.sort_order !== order);
    const key = ["external-links"];
    queryClient.setQueryData<ExternalLink[]>(key, next.map((l, i) => ({ ...l, sort_order: i })));
    setMoving(true);
    const applied: ExternalLink[] = [];
    try {
      for (const { link, order } of changes) {
        await api.patch(`/external-links/${link.id}`, { sort_order: order });
        applied.push(link);
      }
    } catch (e) {
      await Promise.allSettled(applied.map((l) => api.patch(`/external-links/${l.id}`, { sort_order: l.sort_order })));
      toast.error(e instanceof ApiError ? e.detail : t("extLinks.moveError"));
    } finally {
      setMoving(false);
      void queryClient.invalidateQueries({ queryKey: key });
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("extLinks.title")}</CardTitle>
        <CardDescription>
          {t("extLinks.desc")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {items.length === 0 && !links.isLoading && <p className="text-sm text-muted-foreground">{t("extLinks.empty")}</p>}
        <ul className="space-y-1.5">
          {items.map((link, index) => (
            <li key={link.id} className="flex items-center gap-2 text-sm">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{link.name}</div>
                <div className="truncate text-xs text-muted-foreground">
                  {link.url} · {link.open_mode === "iframe" ? t("extLinks.embedded") : t("extLinks.newTabLower")}
                </div>
              </div>
              <Button type="button" variant="ghost" size="icon-sm" aria-label={t("extLinks.moveUp", { name: link.name })} disabled={moving || index === 0} onClick={() => move(index, -1)}>
                <ArrowUp className="size-4" />
              </Button>
              <Button type="button" variant="ghost" size="icon-sm" aria-label={t("extLinks.moveDown", { name: link.name })} disabled={moving || index === items.length - 1} onClick={() => move(index, 1)}>
                <ArrowDown className="size-4" />
              </Button>
              <Button type="button" variant="ghost" size="icon-sm" aria-label={t("extLinks.deleteLink", { name: link.name })} onClick={() => setDeleting(link)}>
                <Trash2 className="size-4" />
              </Button>
            </li>
          ))}
        </ul>
        <form onSubmit={submit} className="grid gap-3 sm:grid-cols-[1fr_2fr_auto_auto] sm:items-end">
          <div className="space-y-1.5">
            <Label htmlFor="ext-name">{t("extLinks.name")}</Label>
            <Input id="ext-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ext-url">{t("extLinks.url")}</Label>
            <Input id="ext-url" type="url" placeholder="https://" value={url} onChange={(e) => setUrl(e.target.value)} required />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ext-mode">{t("extLinks.openIn")}</Label>
            <select
              id="ext-mode"
              value={openMode}
              onChange={(e) => setOpenMode(e.target.value as ExternalLink["open_mode"])}
              className="h-9 rounded-md border border-input bg-background ps-2 text-sm"
            >
              <option value="iframe">{t("extLinks.optEmbedded")}</option>
              <option value="newtab">{t("extLinks.optNewTab")}</option>
            </select>
          </div>
          <Button type="submit" disabled={create.isPending}>
            {t("extLinks.addLink")}
          </Button>
        </form>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error instanceof ApiError ? error.detail : t("extLinks.saveError")}
          </p>
        )}
      </CardContent>
      <ConfirmDialog
        open={deleting != null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={t("extLinks.deleteTitle", { name: deleting?.name ?? "" })}
        description={t("extLinks.deleteDesc")}
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        destructive
        onConfirm={() => deleting && remove.mutate(deleting.id)}
      />
    </Card>
  );
}
