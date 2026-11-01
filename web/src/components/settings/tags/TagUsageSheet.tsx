import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import { useTagUsage } from "@/api/tags";
import type { TagOut } from "@/api/types";
import { TagChip } from "@/components/tags/TagChip";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";

function Section({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  if (count === 0) return null;
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium text-foreground">
        {title} <span className="text-muted-foreground">({count})</span>
      </h3>
      <ul className="space-y-1">{children}</ul>
    </section>
  );
}

const rowClass = "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted";

/** Side sheet listing what carries a tag (models with thumbnails, projects, collections, prints). */
export function TagUsageSheet({ tag, onClose }: { tag: TagOut | null; onClose: () => void }) {
  const { t } = useTranslation();
  const usage = useTagUsage(tag?.id ?? null);
  const d = usage.data;
  const empty = d && d.counts.total === 0;

  return (
    <Sheet open={tag !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle className="flex flex-wrap items-center gap-2">{tag ? <TagChip tag={tag} /> : null}</SheetTitle>
          <SheetDescription>
            {tag ? t("settings.tags.usage.title", { name: tag.name }) : ""} — {t("settings.tags.usage.description")}
          </SheetDescription>
        </SheetHeader>
        <div className="space-y-5 px-4 pb-6">
          {tag && (
            <Button asChild size="sm" variant="outline">
              <Link to="/library" search={{ tags: [tag.id] }}>
                {t("settings.tags.usage.viewInLibrary")}
              </Link>
            </Button>
          )}
          {usage.isLoading ? (
            <Skeleton className="h-32 w-full rounded-lg" />
          ) : usage.isError ? (
            <p role="alert" className="text-sm text-muted-foreground">
              {usage.error instanceof ApiError ? usage.error.detail : t("settings.tags.usage.loadError")}
            </p>
          ) : empty ? (
            <p className="text-sm text-muted-foreground">{t("settings.tags.usage.none")}</p>
          ) : d ? (
            <>
              {d.counts.model_files > 0 && (
                <p className="text-sm text-muted-foreground">
                  {t("settings.tags.usage.modelFiles", { count: d.counts.model_files })}
                </p>
              )}
              <Section title={t("settings.tags.usage.models")} count={d.counts.models}>
                {d.models.map((m) => (
                  <li key={m.id}>
                    <Link to="/models/$slug" params={{ slug: m.slug }} className={rowClass}>
                      {m.thumbnail_url ? (
                        <img
                          src={m.thumbnail_url}
                          alt=""
                          loading="lazy"
                          className="size-10 shrink-0 rounded bg-muted object-cover"
                          onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                        />
                      ) : (
                        <span className="size-10 shrink-0 rounded bg-muted" aria-hidden />
                      )}
                      <span className="truncate">{m.name}</span>
                    </Link>
                  </li>
                ))}
              </Section>
              <Section title={t("settings.tags.usage.projects")} count={d.counts.projects}>
                {d.projects.map((p) => (
                  <li key={p.id}>
                    <Link to="/projects/$id" params={{ id: String(p.id) }} className={rowClass}>
                      <span className="truncate">{p.name}</span>
                      <span className="ml-auto text-xs text-muted-foreground">{p.status}</span>
                    </Link>
                  </li>
                ))}
              </Section>
              <Section title={t("settings.tags.usage.collections")} count={d.counts.collections}>
                {d.collections.map((c) => (
                  <li key={c.id}>
                    <Link to="/collections" className={rowClass}>
                      <span className="truncate">{c.title}</span>
                      <span className="ml-auto text-xs text-muted-foreground">{c.site}</span>
                    </Link>
                  </li>
                ))}
              </Section>
              <Section title={t("settings.tags.usage.archives")} count={d.counts.archives}>
                {d.archives.map((a) => (
                  <li key={a.id}>
                    <Link to="/print/history" search={{ tab: "runs" }} className={rowClass}>
                      {a.has_thumbnail && (
                        <img
                          src={`/api/archives/${a.id}/thumbnail`}
                          alt=""
                          loading="lazy"
                          className="size-10 shrink-0 rounded bg-muted object-cover"
                        />
                      )}
                      <span className="truncate">{a.name || t("settings.tags.usage.untitled")}</span>
                      <span className="ml-auto text-xs text-muted-foreground">{a.result}</span>
                    </Link>
                  </li>
                ))}
              </Section>
            </>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}
