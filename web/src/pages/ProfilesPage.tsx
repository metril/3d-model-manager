/**
 * `/inventory/profiles` (P6 Task 9): slicer profiles and print recipes.
 * Two tabs -- Profiles (a kind-filtered table, zip/sidecar import) and
 * Recipes (a table + `RecipeForm`) -- plus a header chip showing the
 * configured slicer sidecar's health. Mirrors `MaterialsPage`'s
 * list+dialog shape; writes (import, create/edit/delete recipe, delete
 * profile) are hidden without `inventory:write`.
 */
import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { CloudIcon, PlusIcon, Trash2Icon, UploadIcon, WrenchIcon } from "lucide-react";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useDeletePipeline, usePipelines, type SlicerPipeline } from "@/api/pipelines";
import { useDeleteRecipe, useProfileList, useRecipes, useSlicerHealth, type ProfileScope } from "@/api/slicing";
import type { PrintRecipe, ProfileKind } from "@/api/types";
import { CloudPresetsDialog } from "@/components/inventory/CloudPresetsDialog";
import { PipelineForm } from "@/components/inventory/PipelineForm";
import { ProfileImportDialog } from "@/components/inventory/ProfileImportDialog";
import { SyncProfilesButton } from "@/components/inventory/SyncProfilesButton";
import { RecipeForm } from "@/components/inventory/RecipeForm";
import { ProfileScopeBar, ScopeBadges, ScopeHeader } from "@/components/inventory/ProfileScopeBar";
import { isUniversal, profileModelText, profileNozzleText } from "@/lib/profileScope";
import { parseProfilesSearch, type ProfilesSearch } from "@/pages/profilesSearch";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Card, CardContent } from "@/components/ui/card";
import { PageContainer } from "@/components/ui/page-container";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const PROFILE_KINDS: { value: ProfileKind | "all"; label: string }[] = [
  { value: "all", label: "profilesPage.kind.all" },
  { value: "printer", label: "profilesPage.kind.printer" },
  { value: "process", label: "profilesPage.kind.process" },
  { value: "filament", label: "profilesPage.kind.filament" },
];

function SidecarHealthChip() {
  const { t } = useTranslation();
  const health = useSlicerHealth();
  if (health.isLoading) return <Skeleton className="h-5 w-24 rounded-full" />;
  if (!health.data?.configured) return <Badge variant="secondary">{t("profilesPage.sidecarOff")}</Badge>;
  return (
    <Badge variant={health.data.ok ? "outline" : "destructive"}>
      {health.data.ok ? t("profilesPage.sidecarOk", { version: (health.data.version ?? "?").replace(/^v/i, "") }) : t("profilesPage.sidecarDown")}
    </Badge>
  );
}

function ProfilesTab({
  scope,
  kindFilter,
  setKindFilter,
  onAll,
}: {
  scope: ProfileScope;
  kindFilter: ProfileKind | "all";
  setKindFilter: (k: ProfileKind | "all") => void;
  onAll: (all: boolean) => void;
}) {
  const { t } = useTranslation();
  const { can } = useAuth();
  const canWrite = can("inventory:write");
  const [importOpen, setImportOpen] = useState(false);
  const [cloudOpen, setCloudOpen] = useState(false);
  const profileList = useProfileList(kindFilter === "all" ? undefined : kindFilter, {
    ...scope,
    layer: kindFilter === "process" ? scope.layer : undefined,
  });
  const profiles = { ...profileList, data: profileList.data?.items };
  const shown = (profiles.data ?? []).filter((p) => p.in_scope !== false).length;
  const health = useSlicerHealth();

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1">
          {PROFILE_KINDS.map((k) => (
            <Button
              key={k.value}
              type="button"
              size="sm"
              variant={kindFilter === k.value ? "secondary" : "ghost"}
              aria-pressed={kindFilter === k.value}
              onClick={() => setKindFilter(k.value)}
            >
              {t(k.label)}
            </Button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <SidecarHealthChip />
          <Button type="button" size="sm" variant="outline" onClick={() => setCloudOpen(true)}>
            <CloudIcon className="size-4" />
            {t("profilesPage.cloud")}
          </Button>
          {canWrite ? (
            <Button type="button" size="sm" variant="outline" onClick={() => setImportOpen(true)}>
              <UploadIcon className="size-4" />
              {t("profilesPage.import")}
            </Button>
          ) : null}
        </div>
      </div>

      {canWrite && health.data?.configured ? <SyncProfilesButton idPrefix="profiles-sync" /> : null}

      <ScopeHeader
        idPrefix="profiles"
        shown={shown}
        total={profileList.data?.total}
        all={scope.all}
        onAll={onAll}
      />

      <Card>
        <CardContent>
          {profiles.isLoading ? (
            <Skeleton className="h-32 w-full rounded-lg" />
          ) : profiles.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {profiles.error instanceof ApiError ? profiles.error.detail : t("profilesPage.loadProfiles")}
            </p>
          ) : (profiles.data ?? []).length === 0 ? (
            <EmptyState icon={WrenchIcon} title={t("profilesPage.noProfiles")} />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("profilesPage.col.name")}</TableHead>
                  <TableHead>{t("profilesPage.col.printerModel")}</TableHead>
                  <TableHead>{t("profilesPage.col.nozzle")}</TableHead>
                  {kindFilter === "process" ? <TableHead>{t("profilesPage.col.layer")}</TableHead> : null}
                  <TableHead>{t("profilesPage.col.source")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(profiles.data ?? []).map((p) => (
                  <TableRow key={p.id} className={p.in_scope === false ? "opacity-50" : undefined}>
                    <TableCell className="max-w-72 font-medium">
                      <span className="flex items-center gap-1.5">
                        <span className="min-w-0 truncate" title={p.name}>{p.name}</span>
                        <ScopeBadges universal={isUniversal(p)} reason={p.in_scope === false ? p.scope_reason : null} />
                      </span>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {profileModelText(p) ?? <Badge variant="outline">{t("profilesPage.anyPrinter")}</Badge>}
                    </TableCell>
                    <TableCell className="font-mono text-muted-foreground">{profileNozzleText(p) ?? "—"}</TableCell>
                    {kindFilter === "process" ? (
                      <TableCell className="font-mono text-muted-foreground">
                        {p.layer_height_mm != null ? `${p.layer_height_mm} mm` : "—"}
                      </TableCell>
                    ) : null}
                    <TableCell className="whitespace-nowrap">
                      <Badge variant="outline">{p.cloud_source ? t("profilesPage.cloudSource", { source: p.cloud_source }) : p.source}</Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <ProfileImportDialog open={importOpen} onOpenChange={setImportOpen} />
      <CloudPresetsDialog
        open={cloudOpen}
        onOpenChange={setCloudOpen}
        canWrite={canWrite}
        canAdmin={can("settings:write")}
      />
    </div>
  );
}

function RecipesTab({ scope, onAll }: { scope: ProfileScope; onAll: (all: boolean) => void }) {
  const { t } = useTranslation();
  const { can } = useAuth();
  const canWrite = can("inventory:write");
  const recipes = useRecipes({ ...scope, compatibleOnly: !scope.all });
  const shown = (recipes.data ?? []).filter((r) => r.compatible !== false).length;
  const deleteRecipe = useDeleteRecipe();
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<PrintRecipe | undefined>(undefined);

  function openCreate() {
    setEditing(undefined);
    setFormOpen(true);
  }

  function openEdit(recipe: PrintRecipe) {
    setEditing(recipe);
    setFormOpen(true);
  }

  return (
    <div className="flex flex-col gap-4">
      {canWrite ? (
        <div className="flex justify-end">
          <Button type="button" size="sm" onClick={openCreate}>
            <PlusIcon className="size-4" />
            {t("profilesPage.newRecipe")}
          </Button>
        </div>
      ) : null}

      <ScopeHeader
        idPrefix="recipes"
        shown={shown}
        total={scope.all ? (recipes.data ?? []).length : undefined}
        all={scope.all}
        onAll={onAll}
      />

      <Card>
        <CardContent>
          {recipes.isLoading ? (
            <Skeleton className="h-32 w-full rounded-lg" />
          ) : recipes.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {recipes.error instanceof ApiError ? recipes.error.detail : t("profilesPage.loadRecipes")}
            </p>
          ) : (recipes.data ?? []).length === 0 ? (
            <EmptyState icon={WrenchIcon} title={t("profilesPage.noRecipes")} />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("profilesPage.col.name")}</TableHead>
                  <TableHead>{t("profilesPage.col.bedType")}</TableHead>
                  <TableHead>{t("profilesPage.col.fanout")}</TableHead>
                  {canWrite ? <TableHead /> : null}
                </TableRow>
              </TableHeader>
              <TableBody>
                {(recipes.data ?? []).map((r) => (
                  <TableRow key={r.id} className={r.compatible === false ? "opacity-50" : undefined}>
                    <TableCell className="font-medium">
                      <span className="flex flex-wrap items-center gap-1.5">
                        {canWrite ? (
                          <button type="button" className="hover:underline" onClick={() => openEdit(r)}>
                            {r.name}
                          </button>
                        ) : (
                          r.name
                        )}
                        <ScopeBadges universal={false} reason={r.compatible === false ? r.incompatible_reason : null} />
                      </span>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{r.bed_type}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{r.fanout}{r.fanout === "first_n" ? ` (${r.n ?? "?"})` : ""}</Badge>
                    </TableCell>
                    {canWrite ? (
                      <TableCell className="text-right">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          aria-label={t("profilesPage.deleteNamed", { name: r.name })}
                          onClick={() => deleteRecipe.mutate(r.id)}
                        >
                          <Trash2Icon className="size-4" />
                        </Button>
                      </TableCell>
                    ) : null}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <RecipeForm open={formOpen} onOpenChange={setFormOpen} recipe={editing} />
    </div>
  );
}


function PipelinesTab({ scope }: { scope: ProfileScope }) {
  const { t } = useTranslation();
  const { can } = useAuth();
  const canWrite = can("inventory:write");
  const pipelines = usePipelines({ printerId: scope.printerId, nozzle: scope.nozzle, q: scope.q });
  const remove = useDeletePipeline();
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<SlicerPipeline | undefined>(undefined);

  return (
    <div className="flex flex-col gap-4">
      {canWrite ? (
        <div className="flex justify-end">
          <Button
            type="button"
            size="sm"
            onClick={() => {
              setEditing(undefined);
              setFormOpen(true);
            }}
          >
            <PlusIcon className="size-4" />
            {t("profilesPage.newPipeline")}
          </Button>
        </div>
      ) : null}

      <ScopeHeader idPrefix="pipelines" shown={(pipelines.data ?? []).length} />

      <Card>
        <CardContent>
          {pipelines.isLoading ? (
            <Skeleton className="h-32 w-full rounded-lg" />
          ) : pipelines.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {pipelines.error instanceof ApiError ? pipelines.error.detail : t("profilesPage.loadPipelines")}
            </p>
          ) : (pipelines.data ?? []).length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">
              {t("profilesPage.noPipelines")}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("profilesPage.col.name")}</TableHead>
                  <TableHead>{t("profilesPage.col.recipe")}</TableHead>
                  <TableHead>{t("profilesPage.col.steps")}</TableHead>
                  {canWrite ? <TableHead /> : null}
                </TableRow>
              </TableHeader>
              <TableBody>
                {(pipelines.data ?? []).map((p) => (
                  <TableRow key={p.id}>
                    <TableCell className="font-medium">
                      {canWrite ? (
                        <button
                          type="button"
                          className="hover:underline"
                          onClick={() => {
                            setEditing(p);
                            setFormOpen(true);
                          }}
                        >
                          {p.name}
                        </button>
                      ) : (
                        p.name
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{p.recipe_name ?? "—"}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {p.steps.length === 0 ? (
                          <span className="text-muted-foreground">{t("profilesPage.sliceOnly")}</span>
                        ) : (
                          p.steps.map((s) => (
                            <Badge key={s.kind} variant="outline">
                              {t(`profilesPage.step.${s.kind}`)}
                            </Badge>
                          ))
                        )}
                      </div>
                    </TableCell>
                    {canWrite ? (
                      <TableCell className="text-right">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          aria-label={t("profilesPage.deleteNamed", { name: p.name })}
                          onClick={() => remove.mutate(p.id)}
                        >
                          <Trash2Icon className="size-4" />
                        </Button>
                      </TableCell>
                    ) : null}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {remove.isError ? (
            <p role="alert" className="mt-2 text-sm text-destructive">
              {remove.error instanceof ApiError ? remove.error.detail : t("profilesPage.deletePipeline")}
            </p>
          ) : null}
        </CardContent>
      </Card>

      <PipelineForm open={formOpen} onOpenChange={setFormOpen} pipeline={editing} />
    </div>
  );
}

export function ProfilesPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const search = parseProfilesSearch(useSearch({ strict: false }) as Record<string, unknown>);
  const tab = search.tab ?? "profiles";
  const [kindFilter, setKindFilter] = useState<ProfileKind | "all">("all");
  function patchSearch(patch: Partial<ProfilesSearch>) {
    void navigate({
      to: ".",
      search: ((prev: Record<string, unknown>) => ({ ...prev, ...patch })) as never,
      replace: true,
    });
  }
  const scope: ProfileScope = {
    printerId: search.printer,
    nozzle: search.nozzle,
    layer: search.layer,
    q: search.q,
    all: search.all === 1,
  };
  const onAll = (all: boolean) => patchSearch({ all: all ? 1 : undefined });
  const showLayer = tab === "recipes" || (tab === "profiles" && kindFilter === "process");

  return (
    <PageContainer width="default">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">{t("profilesPage.title")}</h1>
        <p className="text-sm text-muted-foreground">
          {t("profilesPage.subtitle")}
        </p>
      </div>

      <ProfileScopeBar search={search} onPatch={patchSearch} showLayer={showLayer} />

      <Tabs
        value={tab}
        onValueChange={(v) => patchSearch({ tab: v === "profiles" ? undefined : (v as "recipes" | "pipelines") })}
      >
        <TabsList>
          <TabsTrigger value="profiles">{t("profilesPage.tab.profiles")}</TabsTrigger>
          <TabsTrigger value="recipes">{t("profilesPage.tab.recipes")}</TabsTrigger>
          <TabsTrigger value="pipelines">{t("profilesPage.tab.pipelines")}</TabsTrigger>
        </TabsList>
        <TabsContent value="profiles">
          <ProfilesTab scope={scope} kindFilter={kindFilter} setKindFilter={setKindFilter} onAll={onAll} />
        </TabsContent>
        <TabsContent value="recipes">
          <RecipesTab scope={scope} onAll={onAll} />
        </TabsContent>
        <TabsContent value="pipelines">
          <PipelinesTab scope={scope} />
        </TabsContent>
      </Tabs>
    </PageContainer>
  );
}
