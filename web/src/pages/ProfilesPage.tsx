/**
 * `/inventory/profiles` (P6 Task 9): slicer profiles and print recipes.
 * Two tabs -- Profiles (a kind-filtered table, zip/sidecar import) and
 * Recipes (a table + `RecipeForm`) -- plus a header chip showing the
 * configured slicer sidecar's health. Mirrors `MaterialsPage`'s
 * list+dialog shape; writes (import, create/edit/delete recipe, delete
 * profile) are hidden without `inventory:write`.
 */
import { useState } from "react";
import { PlusIcon, Trash2Icon, UploadIcon } from "lucide-react";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useDeleteRecipe, useProfiles, useRecipes, useSlicerHealth } from "@/api/slicing";
import type { PrintRecipe, ProfileKind } from "@/api/types";
import { ProfileImportDialog } from "@/components/inventory/ProfileImportDialog";
import { SyncProfilesButton } from "@/components/inventory/SyncProfilesButton";
import { RecipeForm } from "@/components/inventory/RecipeForm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
            <div className="py-8 text-center text-sm text-muted-foreground">No profiles yet.</div>
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
                      <Badge variant="outline">{p.source}</Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <ProfileImportDialog open={importOpen} onOpenChange={setImportOpen} />
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
            <div className="py-8 text-center text-sm text-muted-foreground">No recipes yet.</div>
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

export function ProfilesPage() {
  const [tab, setTab] = useState<"profiles" | "recipes">("profiles");

  return (
    <PageContainer width="default">
      <div>
        <h1 className="text-lg font-semibold text-foreground">Profiles & recipes</h1>
        <p className="text-sm text-muted-foreground">
          Slicer profiles imported from a bundle or sidecar, and the recipes that combine them.
        </p>
      </div>

      <Tabs value={tab} onValueChange={(v) => setTab(v as "profiles" | "recipes")}>
        <TabsList>
          <TabsTrigger value="profiles">Profiles</TabsTrigger>
          <TabsTrigger value="recipes">Recipes</TabsTrigger>
        </TabsList>
        <TabsContent value="profiles">
          <ProfilesTab />
        </TabsContent>
        <TabsContent value="recipes">
          <RecipesTab />
        </TabsContent>
      </Tabs>
    </PageContainer>
  );
}
