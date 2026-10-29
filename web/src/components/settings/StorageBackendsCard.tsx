/**
 * Storage backends list: the full `storage_backends` table -- the SOLE
 * storage UI (the legacy single-backend `StorageSettingsCard` was removed in
 * M8 F). Each row: name, scheme, a "Default" badge, and Test/Set default/Move
 * all here/Edit/Delete actions. Add/Edit reuse `StorageBackendForm`
 * (+ its `seedDraft`/`missingRequiredFields`/`stripBlankSecrets` helpers) in
 * a `Dialog`, same shape as the legacy card's own form -- just scoped to one
 * `storage_backends` row instead of "the" active config. Delete goes through
 * `ConfirmDialog` and surfaces the backend's 409 guardrail (last backend,
 * default backend, or one still holding files) as a plain error message
 * rather than trying to predict it client-side.
 */
import { useState, type FormEvent, type ReactNode } from "react";

import { ApiError } from "@/api/client";
import {
  useCreateBackend,
  useDeleteBackend,
  useMigrateToBackend,
  useSetDefaultBackend,
  useStorageBackends,
  useTestBackend,
  useUpdateBackend,
} from "@/api/settings";
import type { StorageBackendOut, StorageConfigIn, StorageConfigOut } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import {
  BACKEND_LABELS,
  missingRequiredFields,
  seedDraft,
  StorageBackendForm,
  stripBlankSecrets,
} from "@/components/settings/StorageBackendForm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useDiscardGuard } from "@/components/ui/use-discard-guard";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

function asConfigOut(backend: StorageBackendOut): StorageConfigOut {
  return { backend: backend.scheme, config: backend.config };
}

/** `StorageBackendCreateIn`/`StorageBackendUpdateIn.config` needs its OWN
 * `backend` discriminator field inside it (`_parse_backend_config` in
 * `app.api.settings`) -- unlike the legacy shim's flat `{backend, config}`
 * body, where `config` alone is never validated on its own. `draft.config`
 * only carries that key when the draft was seeded from an existing row's
 * (already-`backend`-embedding) `StorageBackendOut.config` (`seedDraft` just
 * copies it); a brand-new draft's `config` starts at `{}` and never gets one
 * set by `StorageBackendForm`'s own backend-type picker (which resets
 * `config` to `{}`, not `{backend: ...}`). Stamping it here, unconditionally,
 * covers both cases without relying on which one produced `draft`. */
function backendPayloadConfig(draft: StorageConfigIn): Record<string, unknown> {
  const stripped = stripBlankSecrets(draft);
  return { ...stripped.config, backend: stripped.backend };
}

/** Add (`backend` omitted) or edit (`backend` given) a `storage_backends`
 * row. Fully self-contained -- owns its own open state, draft, and mutation
 * so both the card's "Add backend" trigger and each row's "Edit" trigger can
 * mount one independently. */
function BackendFormDialog({ trigger, backend }: { trigger: ReactNode; backend?: StorageBackendOut }) {
  const isEdit = backend !== undefined;
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(backend?.name ?? "");
  const [draft, setDraft] = useState<StorageConfigIn>(() =>
    backend ? seedDraft(asConfigOut(backend)) : { backend: "local", config: {} },
  );

  const createBackend = useCreateBackend();
  const updateBackend = useUpdateBackend();
  const mutation = isEdit ? updateBackend : createBackend;

  const missing = missingRequiredFields(draft, backend ? asConfigOut(backend) : undefined);
  const nameMissing = name.trim().length === 0;
  const isReady = missing.length === 0 && !nameMissing;

  const dirty =
    JSON.stringify([name, draft]) !==
    JSON.stringify([backend?.name ?? "", backend ? seedDraft(asConfigOut(backend)) : { backend: "local", config: {} }]);

  function reset() {
    setName(backend?.name ?? "");
    setDraft(backend ? seedDraft(asConfigOut(backend)) : { backend: "local", config: {} });
    createBackend.reset();
    updateBackend.reset();
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const config = backendPayloadConfig(draft);
    if (isEdit) {
      updateBackend.mutate(
        { id: backend.id, payload: { name, config } },
        { onSuccess: () => setOpen(false) },
      );
    } else {
      createBackend.mutate({ name, config }, { onSuccess: () => { setOpen(false); reset(); } });
    }
  }

  const { requestChange, confirm } = useDiscardGuard({
    onOpenChange: (next) => {
      setOpen(next);
      if (!next) reset();
    },
    dirty,
    busy: mutation.isPending,
  });

  return (
    <>
    <Dialog open={open} onOpenChange={requestChange}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent dismissable={false}>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>{isEdit ? `Edit "${backend.name}"` : "Add storage backend"}</DialogTitle>
            <DialogDescription>
              {isEdit
                ? "Update this backend's connection details. A blank secret field keeps the stored value."
                : "Configure a new backend that library files can be stored on."}
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-4 py-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="backend-name">Name</Label>
              <Input
                id="backend-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                disabled={mutation.isPending}
                required
                autoFocus
              />
            </div>
            <StorageBackendForm
              value={draft}
              onChange={setDraft}
              disabled={mutation.isPending}
              idPrefix={isEdit ? `backend-${backend.id}` : "backend-new"}
            />
            {missing.length > 0 ? (
              <p role="alert" className="text-sm text-destructive">
                Fill in: {missing.join(", ")}
              </p>
            ) : null}
            {mutation.isError ? (
              <p role="alert" className="text-sm text-destructive">
                {mutation.error instanceof ApiError ? mutation.error.detail : "Could not save this backend"}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button type="submit" disabled={!isReady || mutation.isPending}>
              {mutation.isPending ? "Saving..." : isEdit ? "Save changes" : "Add backend"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
    {confirm}
    </>
  );
}

function BackendRow({ backend }: { backend: StorageBackendOut }) {
  const testBackend = useTestBackend();
  const setDefaultBackend = useSetDefaultBackend();
  const deleteBackend = useDeleteBackend();
  const migrateBackend = useMigrateToBackend();

  const testingThis = testBackend.isPending && testBackend.variables === backend.id;
  const testResultForThis = (testBackend.isSuccess || testBackend.isError) && testBackend.variables === backend.id;
  const settingDefaultThis = setDefaultBackend.isPending && setDefaultBackend.variables === backend.id;
  const defaultErrorForThis = setDefaultBackend.isError && setDefaultBackend.variables === backend.id;
  const deletingThis = deleteBackend.isPending && deleteBackend.variables === backend.id;
  const deleteErrorForThis = deleteBackend.isError && deleteBackend.variables === backend.id;
  const migratingThis = migrateBackend.isPending && migrateBackend.variables === backend.id;
  const migrateErrorForThis = migrateBackend.isError && migrateBackend.variables === backend.id;

  return (
    <TableRow>
      <TableCell className="font-medium text-foreground">{backend.name}</TableCell>
      <TableCell className="text-muted-foreground">{BACKEND_LABELS[backend.scheme] ?? backend.scheme}</TableCell>
      <TableCell>{backend.is_default ? <Badge>Default</Badge> : null}</TableCell>
      <TableCell className="whitespace-normal">
        <div className="flex flex-wrap items-center gap-1.5">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={testingThis}
            onClick={() => testBackend.mutate(backend.id)}
          >
            {testingThis ? "Testing..." : "Test"}
          </Button>
          {!backend.is_default ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={settingDefaultThis}
              onClick={() => setDefaultBackend.mutate(backend.id)}
            >
              {settingDefaultThis ? "Setting..." : "Set default"}
            </Button>
          ) : null}
          {!backend.is_default ? (
            <ConfirmDialog
              trigger={
                <Button type="button" size="sm" variant="outline" disabled={migratingThis}>
                  {migratingThis ? "Moving..." : "Move all here"}
                </Button>
              }
              title={`Move the whole library to "${backend.name}"?`}
              description="Makes this backend the default and moves every model's files onto it (copy, verify, then remove the source). Runs as a job you can watch on the Jobs page."
              confirmLabel="Move all here"
              onConfirm={() => migrateBackend.mutate(backend.id)}
            />
          ) : null}
          <BackendFormDialog
            backend={backend}
            trigger={
              <Button type="button" size="sm" variant="outline">
                Edit
              </Button>
            }
          />
          <ConfirmDialog
            trigger={
              <Button type="button" size="sm" variant="destructive" disabled={deletingThis}>
                {deletingThis ? "Deleting..." : "Delete"}
              </Button>
            }
            title={`Delete "${backend.name}"?`}
            description="This only removes the backend's configuration -- it never deletes files. The default backend, the last remaining backend, or one still holding files can't be deleted this way."
            confirmLabel="Delete"
            destructive
            onConfirm={() => deleteBackend.mutate(backend.id)}
          />
        </div>
        {testResultForThis ? (
          testBackend.isError ? (
            <p role="alert" className="mt-1 text-xs text-destructive">
              {testBackend.error instanceof ApiError ? testBackend.error.detail : "Could not test connection"}
            </p>
          ) : (
            <p
              role={testBackend.data.ok ? undefined : "alert"}
              className={
                testBackend.data.ok ? "mt-1 text-xs text-success" : "mt-1 text-xs text-destructive"
              }
            >
              {testBackend.data.detail} ({testBackend.data.latency_ms} ms)
            </p>
          )
        ) : null}
        {defaultErrorForThis ? (
          <p role="alert" className="mt-1 text-xs text-destructive">
            {setDefaultBackend.error instanceof ApiError ? setDefaultBackend.error.detail : "Could not set default backend"}
          </p>
        ) : null}
        {migrateErrorForThis ? (
          <p role="alert" className="mt-1 text-xs text-destructive">
            {migrateBackend.error instanceof ApiError ? migrateBackend.error.detail : "Could not move the library"}
          </p>
        ) : null}
        {deleteErrorForThis ? (
          <p role="alert" className="mt-1 text-xs text-destructive">
            {deleteBackend.error instanceof ApiError ? deleteBackend.error.detail : "Could not delete this backend"}
          </p>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

export function StorageBackendsCard() {
  const backendsQuery = useStorageBackends();
  const backends = backendsQuery.data ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Storage backends</CardTitle>
        <CardDescription>
          Run more than one backend at once -- the default is where new files are written; others can hold moved or
          replicated files.
        </CardDescription>
        <CardAction>
          <BackendFormDialog
            trigger={
              <Button type="button" variant="outline">
                Add backend
              </Button>
            }
          />
        </CardAction>
      </CardHeader>
      <CardContent>
        {backendsQuery.isLoading ? (
          <Skeleton className="h-24 w-full rounded-lg" />
        ) : backendsQuery.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {backendsQuery.error instanceof ApiError ? backendsQuery.error.detail : "Could not load storage backends"}
          </p>
        ) : backends.length === 0 ? (
          <p className="text-sm text-muted-foreground">No storage backends configured.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Scheme</TableHead>
                <TableHead>Default</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {backends.map((backend) => (
                <BackendRow key={backend.id} backend={backend} />
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
