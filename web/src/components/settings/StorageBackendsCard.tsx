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
import { useTranslation } from "react-i18next";
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
  backendLabels,
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
  const { t } = useTranslation();
  const isEdit = backend !== undefined;
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(backend?.name ?? "");
  const [draft, setDraft] = useState<StorageConfigIn>(() =>
    backend ? seedDraft(asConfigOut(backend)) : { backend: "local", config: {} },
  );

  const createBackend = useCreateBackend();
  const updateBackend = useUpdateBackend();
  const mutation = isEdit ? updateBackend : createBackend;

  const missing = missingRequiredFields(draft, backend ? asConfigOut(backend) : undefined, t);
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
      createBackend.mutate({ name, config }, { onSuccess: () => setOpen(false) });
    }
  }

  const { requestChange, confirm } = useDiscardGuard({
    onOpenChange: (next) => {
      // Re-snapshot on every open and close: fresh props, no stale secrets.
      reset();
      setOpen(next);
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
            <DialogTitle>{isEdit ? t("settingsStorage.editTitle", { name: backend.name }) : t("settingsStorage.addTitle")}</DialogTitle>
            <DialogDescription>
              {isEdit
                ? t("settingsStorage.editDesc")
                : t("settingsStorage.addDesc")}
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-4 py-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="backend-name">{t("settingsStorage.name")}</Label>
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
                {t("settingsStorage.fillIn", { fields: missing.join(", ") })}
              </p>
            ) : null}
            {mutation.isError ? (
              <p role="alert" className="text-sm text-destructive">
                {mutation.error instanceof ApiError ? mutation.error.detail : t("settingsStorage.saveError")}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button type="submit" disabled={!isReady || mutation.isPending}>
              {mutation.isPending ? t("settingsStorage.saving") : isEdit ? t("settingsStorage.saveChanges") : t("settingsStorage.addBackend")}
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
  const { t } = useTranslation();
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
      <TableCell className="text-muted-foreground">{backendLabels(t)[backend.scheme] ?? backend.scheme}</TableCell>
      <TableCell>{backend.is_default ? <Badge>{t("settingsStorage.default")}</Badge> : null}</TableCell>
      <TableCell className="whitespace-normal">
        <div className="flex flex-wrap items-center gap-1.5">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={testingThis}
            onClick={() => testBackend.mutate(backend.id)}
          >
            {testingThis ? t("settingsStorage.testing") : t("settingsStorage.test")}
          </Button>
          {!backend.is_default ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={settingDefaultThis}
              onClick={() => setDefaultBackend.mutate(backend.id)}
            >
              {settingDefaultThis ? t("settingsStorage.setting") : t("settingsStorage.setDefault")}
            </Button>
          ) : null}
          {!backend.is_default ? (
            <ConfirmDialog
              trigger={
                <Button type="button" size="sm" variant="outline" disabled={migratingThis}>
                  {migratingThis ? t("settingsStorage.moving") : t("settingsStorage.moveAll")}
                </Button>
              }
              title={t("settingsStorage.moveTitle", { name: backend.name })}
              description={t("settingsStorage.moveDesc")}
              confirmLabel={t("settingsStorage.moveAll")}
              onConfirm={() => migrateBackend.mutate(backend.id)}
            />
          ) : null}
          <BackendFormDialog
            backend={backend}
            trigger={
              <Button type="button" size="sm" variant="outline">
                {t("common.edit")}
              </Button>
            }
          />
          <ConfirmDialog
            trigger={
              <Button type="button" size="sm" variant="destructive" disabled={deletingThis}>
                {deletingThis ? t("settingsStorage.deleting") : t("common.delete")}
              </Button>
            }
            title={t("settingsStorage.deleteTitle", { name: backend.name })}
            description={t("settingsStorage.deleteDesc")}
            confirmLabel={t("common.delete")}
            destructive
            onConfirm={() => deleteBackend.mutate(backend.id)}
          />
        </div>
        {testResultForThis ? (
          testBackend.isError ? (
            <p role="alert" className="mt-1 text-xs text-destructive">
              {testBackend.error instanceof ApiError ? testBackend.error.detail : t("settingsStorage.testError")}
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
            {setDefaultBackend.error instanceof ApiError ? setDefaultBackend.error.detail : t("settingsStorage.defaultError")}
          </p>
        ) : null}
        {migrateErrorForThis ? (
          <p role="alert" className="mt-1 text-xs text-destructive">
            {migrateBackend.error instanceof ApiError ? migrateBackend.error.detail : t("settingsStorage.moveError")}
          </p>
        ) : null}
        {deleteErrorForThis ? (
          <p role="alert" className="mt-1 text-xs text-destructive">
            {deleteBackend.error instanceof ApiError ? deleteBackend.error.detail : t("settingsStorage.deleteError")}
          </p>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

export function StorageBackendsCard() {
  const { t } = useTranslation();
  const backendsQuery = useStorageBackends();
  const backends = backendsQuery.data ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settingsStorage.title")}</CardTitle>
        <CardDescription>
          {t("settingsStorage.description")}
        </CardDescription>
        <CardAction>
          <BackendFormDialog
            trigger={
              <Button type="button" variant="outline">
                {t("settingsStorage.addBackend")}
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
            {backendsQuery.error instanceof ApiError ? backendsQuery.error.detail : t("settingsStorage.loadError")}
          </p>
        ) : backends.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("settingsStorage.none")}</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("settingsStorage.name")}</TableHead>
                <TableHead>{t("settingsStorage.scheme")}</TableHead>
                <TableHead>{t("settingsStorage.default")}</TableHead>
                <TableHead>{t("settingsStorage.actions")}</TableHead>
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
