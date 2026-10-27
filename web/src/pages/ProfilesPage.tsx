/**
 * `/inventory/profiles` (P6 Task 9): slicer profiles and print recipes.
 * Two tabs -- Profiles (a kind-filtered table, zip/sidecar import) and
 * Recipes (a table + `RecipeForm`) -- plus a header chip showing the
 * configured slicer sidecar's health. Mirrors `MaterialsPage`'s
 * list+dialog shape; writes (import, create/edit/delete recipe, delete
 * profile) are hidden without `inventory:write`.
 */
import { useState } from "react";
import { CloudIcon, PlusIcon, Trash2Icon, UploadIcon, WrenchIcon } from "lucide-react";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useDeletePipeline, usePipelines, type SlicerPipeline } from "@/api/pipelines";
import { useDeleteRecipe, useProfiles, useRecipes, useSlicerHealth } from "@/api/slicing";
import type { PrintRecipe, ProfileKind } from "@/api/types";
import { CloudPresetsDialog } from "@/components/inventory/CloudPresetsDialog";
import { PipelineForm } from "@/components/inventory/PipelineForm";
import { ProfileImportDialog } from "@/components/inventory/ProfileImportDialog";
import { SyncProfilesButton } from "@/components/inventory/SyncProfilesButton";
import { RecipeForm } from "@/components/inventory/RecipeForm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Card, CardContent } from "@/components/ui/card";
import { PageContainer } from "@/components/ui/page-container";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const PROFILE_KINDS: { value: ProfileKind | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "printer", label: "Printer" },
  { value: "process", label: "Process" },
  { value: "filament", label: "Filament" },
];

function SidecarHealthChip() {
  const health = useSlicerHealth();
  if (health.isLoading) return <Skeleton className="h-5 w-24 rounded-full" />;
  if (!health.data?.configured) return <Badge variant="secondary">Sidecar not configured</Badge>;
  return (
    <Badge variant={health.data.ok ? "outline" : "destructive"}>
      Sidecar {health.data.ok ? `v${health.data.version ?? "?"}` : "unreachable"}
    </Badge>
  );
}

function ProfilesTab() {
  const { can } = useAuth();
  const canWrite = can("inventory:write");
  const [kindFilter, setKindFilter] = useState<ProfileKind | "all">("all");
  const [importOpen, setImportOpen] = useState(false);
  const [cloudOpen, setCloudOpen] = useState(false);
  const profiles = useProfiles(kindFilter === "all" ? undefined : kindFilter);
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
              {k.label}
            </Button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <SidecarHealthChip />
          <Button type="button" size="sm" variant="outline" onClick={() => setCloudOpen(true)}>
            <CloudIcon className="size-4" />
            Cloud presets
          </Button>
          {canWrite ? (
            <Button type="button" size="sm" variant="outline" onClick={() => setImportOpen(true)}>
              <UploadIcon className="size-4" />
              Import
            </Button>
          ) : null}
        </div>
      </div>

      {canWrite && health.data?.configured ? <SyncProfilesButton idPrefix="profiles-sync" /> : null}

      <Card>
        <CardContent>
          {profiles.isLoading ? (
            <Skeleton className="h-32 w-full rounded-lg" />
          ) : profiles.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {profiles.error instanceof ApiError ? profiles.error.detail : "Couldn't load profiles."}
            </p>
          ) : (profiles.data ?? []).length === 0 ? (
            <EmptyState icon={WrenchIcon} title="No profiles yet." />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Printer model</TableHead>
                  <TableHead>Nozzle</TableHead>
                  <TableHead>Source</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(profiles.data ?? []).map((p) => (
                  <TableRow key={p.id}>
                    <TableCell className="font-medium">{p.name}</TableCell>
                    <TableCell className="text-muted-foreground">{p.printer_model ?? "—"}</TableCell>
                    <TableCell className="font-mono text-muted-foreground">
                      {p.nozzle_mm != null ? `${p.nozzle_mm} mm` : "—"}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{p.cloud_source ? `${p.cloud_source} cloud` : p.source}</Badge>
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

function RecipesTab() {
  const { can } = useAuth();
  const canWrite = can("inventory:write");
  const recipes = useRecipes();
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
            New recipe
          </Button>
        </div>
      ) : null}

      <Card>
        <CardContent>
          {recipes.isLoading ? (
            <Skeleton className="h-32 w-full rounded-lg" />
          ) : recipes.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {recipes.error instanceof ApiError ? recipes.error.detail : "Couldn't load recipes."}
            </p>
          ) : (recipes.data ?? []).length === 0 ? (
            <EmptyState icon={WrenchIcon} title="No recipes yet." />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Bed type</TableHead>
                  <TableHead>Fanout</TableHead>
                  {canWrite ? <TableHead /> : null}
                </TableRow>
              </TableHeader>
              <TableBody>
                {(recipes.data ?? []).map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="font-medium">
                      {canWrite ? (
                        <button type="button" className="hover:underline" onClick={() => openEdit(r)}>
                          {r.name}
                        </button>
                      ) : (
                        r.name
                      )}
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
                          aria-label={`Delete ${r.name}`}
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

const STEP_LABEL: Record<string, string> = { orient: "Orient", arrange: "Arrange", rename: "Rename", queue: "Queue" };

function PipelinesTab() {
  const { can } = useAuth();
  const canWrite = can("inventory:write");
  const pipelines = usePipelines();
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
            New pipeline
          </Button>
        </div>
      ) : null}

      <Card>
        <CardContent>
          {pipelines.isLoading ? (
            <Skeleton className="h-32 w-full rounded-lg" />
          ) : pipelines.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {pipelines.error instanceof ApiError ? pipelines.error.detail : "Couldn't load pipelines."}
            </p>
          ) : (pipelines.data ?? []).length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">
              No pipelines yet. A pipeline runs a recipe with extra steps (orient, arrange, rename, queue) in one
              click from a file&apos;s Slice dialog.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Recipe</TableHead>
                  <TableHead>Steps</TableHead>
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
                          <span className="text-muted-foreground">slice only</span>
                        ) : (
                          p.steps.map((s) => (
                            <Badge key={s.kind} variant="outline">
                              {STEP_LABEL[s.kind]}
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
                          aria-label={`Delete ${p.name}`}
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
              {remove.error instanceof ApiError ? remove.error.detail : "Couldn't delete the pipeline."}
            </p>
          ) : null}
        </CardContent>
      </Card>

      <PipelineForm open={formOpen} onOpenChange={setFormOpen} pipeline={editing} />
    </div>
  );
}

export function ProfilesPage() {
  const [tab, setTab] = useState<"profiles" | "recipes" | "pipelines">("profiles");

  return (
    <PageContainer width="default">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Profiles & recipes</h1>
        <p className="text-sm text-muted-foreground">
          Slicer profiles imported from a bundle, the sidecar or your cloud accounts, the recipes that combine them, and the pipelines that run them.
        </p>
      </div>

      <Tabs value={tab} onValueChange={(v) => setTab(v as "profiles" | "recipes" | "pipelines")}>
        <TabsList>
          <TabsTrigger value="profiles">Profiles</TabsTrigger>
          <TabsTrigger value="recipes">Recipes</TabsTrigger>
          <TabsTrigger value="pipelines">Pipelines</TabsTrigger>
        </TabsList>
        <TabsContent value="profiles">
          <ProfilesTab />
        </TabsContent>
        <TabsContent value="recipes">
          <RecipesTab />
        </TabsContent>
        <TabsContent value="pipelines">
          <PipelinesTab />
        </TabsContent>
      </Tabs>
    </PageContainer>
  );
}
