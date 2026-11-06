/** Admin: OIDC (SSO) providers. The client secret is write-only. */
import { useState } from "react";
import { CheckIcon, CopyIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  useAdminOidcProviders,
  useCreateOidcProvider,
  useDeleteOidcProvider,
  usePatchOidcProvider,
} from "@/api/admin";
import { ApiError } from "@/api/client";
import type { OidcProviderOut } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { SwitchRow } from "@/components/ui/switch-row";

function errText(e: unknown, fallback: string) {
  return e instanceof ApiError ? e.detail : fallback;
}

export function OidcProvidersCard() {
  const { t } = useTranslation();
  const providers = useAdminOidcProviders();
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [deleting, setDeleting] = useState<OidcProviderOut | null>(null);
  const del = useDeleteOidcProvider();

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("oidc.title")}</CardTitle>
        <CardDescription>{t("oidc.desc")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {providers.isLoading ? (
          <Skeleton className="h-16 w-full rounded-lg" />
        ) : providers.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {errText(providers.error, t("oidc.loadError"))}
          </p>
        ) : (
          <ul className="divide-y">
            {(providers.data ?? []).map((p) => (
              <li key={p.id} className="space-y-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{p.name}</span>
                  {!p.enabled && <span className="text-sm text-muted-foreground">{t("oidc.disabled")}</span>}
                  <div className="ml-auto flex gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      aria-label={t("oidc.editP", { name: p.name })}
                      onClick={() => setEditing(editing === p.id ? null : p.id)}
                    >
                      {editing === p.id ? t("common.close") : t("common.edit")}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="destructive"
                      aria-label={t("oidc.deleteP", { name: p.name })}
                      onClick={() => setDeleting(p)}
                    >
                      {t("common.delete")}
                    </Button>
                  </div>
                </div>
                {editing === p.id && <ProviderForm provider={p} onDone={() => setEditing(null)} />}
              </li>
            ))}
          </ul>
        )}
        {del.isError && (
          <p role="alert" className="text-sm text-destructive">
            {errText(del.error, t("oidc.deleteError"))}
          </p>
        )}
        {editing === "new" ? (
          <ProviderForm provider={null} onDone={() => setEditing(null)} />
        ) : (
          <Button type="button" variant="outline" onClick={() => setEditing("new")}>
            {t("oidc.add")}
          </Button>
        )}
      </CardContent>
      <ConfirmDialog
        open={deleting != null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={t("oidc.deleteTitle", { name: deleting?.name ?? "" })}
        description={t("oidc.deleteDesc")}
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        destructive
        onConfirm={() => deleting && del.mutate(deleting.id)}
      />
    </Card>
  );
}

function ProviderForm({ provider, onDone }: { provider: OidcProviderOut | null; onDone: () => void }) {
  const { t } = useTranslation();
  const create = useCreateOidcProvider();
  const patch = usePatchOidcProvider();
  const [name, setName] = useState(provider?.name ?? "");
  const [slug, setSlug] = useState(provider?.slug ?? "");
  const [discovery, setDiscovery] = useState(provider?.discovery_url ?? "");
  const [clientId, setClientId] = useState(provider?.client_id ?? "");
  const [secret, setSecret] = useState("");
  const [scopes, setScopes] = useState(provider?.scopes ?? "openid profile email groups");
  const [groupsClaim, setGroupsClaim] = useState(provider?.groups_claim ?? "groups");
  const [adminGroup, setAdminGroup] = useState(provider?.admin_group ?? "");
  const [mapping, setMapping] = useState<{ claim: string; groupId: string }[]>(() =>
    Object.entries(provider?.group_mapping ?? {}).map(([claim, id]) => ({ claim, groupId: String(id) })),
  );
  const [autoCreate, setAutoCreate] = useState(provider?.auto_create_users ?? false);
  const [linkEmail, setLinkEmail] = useState(provider?.link_by_email ?? false);
  const [enabled, setEnabled] = useState(provider?.enabled ?? true);
  const [trustsMfa, setTrustsMfa] = useState(provider?.trusts_idp_mfa ?? false);
  const [copied, setCopied] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const mut = provider ? patch : create;

  function submit() {
    const group_mapping: Record<string, number> = {};
    for (const row of mapping) {
      const claim = row.claim.trim();
      if (!claim && !row.groupId.trim()) continue; // blank spare row
      const id = Number(row.groupId);
      if (!claim || !Number.isInteger(id) || id <= 0) {
        setLocalError(t("oidc.mappingInvalid"));
        return;
      }
      if (claim in group_mapping) {
        setLocalError(t("oidc.mappingDuplicate", { claim }));
        return;
      }
      group_mapping[claim] = id;
    }
    setLocalError(null);
    const body = {
      name: name.trim(),
      slug: slug.trim(),
      discovery_url: discovery.trim(),
      client_id: clientId.trim(),
      scopes,
      groups_claim: groupsClaim,
      group_mapping,
      admin_group: adminGroup.trim() || null,
      auto_create_users: autoCreate,
      link_by_email: linkEmail,
      enabled,
      trusts_idp_mfa: trustsMfa,
    };
    // Blank secret on edit is omitted entirely so the stored one is kept.
    if (provider) {
      patch.mutate(
        { id: provider.id, body: secret ? { ...body, client_secret: secret } : body },
        { onSuccess: onDone },
      );
    } else {
      create.mutate({ ...body, client_secret: secret }, { onSuccess: onDone });
    }
  }

  function copy() {
    if (!provider) return;
    void navigator.clipboard.writeText(provider.callback_url).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  }

  const field = (id: string, label: string, value: string, set: (v: string) => void, extra?: object) => (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} value={value} onChange={(e) => set(e.target.value)} {...extra} />
    </div>
  );

  return (
    <div className="space-y-3 rounded-lg border p-3">
      {provider && (
        <div className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">{t("oidc.callback")}</span>
          <code className="break-all">{provider.callback_url}</code>
          <Button type="button" size="icon-sm" variant="ghost" aria-label={t("oidc.copyCallback")} onClick={copy}>
            {copied ? <CheckIcon /> : <CopyIcon />}
          </Button>
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        {field("oidc-name", t("oidc.name"), name, setName)}
        {field("oidc-slug", t("oidc.slug"), slug, setSlug)}
        {field("oidc-discovery", t("oidc.discovery"), discovery, setDiscovery)}
        {field("oidc-client-id", t("oidc.clientId"), clientId, setClientId)}
        {field("oidc-secret", t("oidc.secret"), secret, setSecret, {
          type: "password",
          autoComplete: "new-password",
          placeholder: provider?.has_client_secret ? t("oidc.keepSecret") : "",
        })}
        {field("oidc-scopes", t("oidc.scopes"), scopes, setScopes)}
        {field("oidc-groups-claim", t("oidc.groupsClaim"), groupsClaim, setGroupsClaim)}
        {field("oidc-admin-group", t("oidc.adminGroup"), adminGroup, setAdminGroup)}
      </div>
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">{t("oidc.mapping")}</legend>
        <p className="text-xs text-muted-foreground">{t("oidc.mappingHint")}</p>
        {mapping.map((row, i) => (
          <div key={i} className="flex items-center gap-2">
            <Input
              aria-label={t("oidc.mappingClaim", { n: i + 1 })}
              placeholder={t("oidc.mappingClaimPh")}
              value={row.claim}
              onChange={(e) => setMapping((m) => m.map((r, j) => (j === i ? { ...r, claim: e.target.value } : r)))}
            />
            <Input
              aria-label={t("oidc.mappingGroup", { n: i + 1 })}
              type="number"
              min={1}
              className="w-28"
              placeholder={t("oidc.mappingGroupPh")}
              value={row.groupId}
              onChange={(e) => setMapping((m) => m.map((r, j) => (j === i ? { ...r, groupId: e.target.value } : r)))}
            />
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              aria-label={t("oidc.mappingRemove", { n: i + 1 })}
              onClick={() => setMapping((m) => m.filter((_, j) => j !== i))}
            >
              <Trash2Icon />
            </Button>
          </div>
        ))}
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => setMapping((m) => [...m, { claim: "", groupId: "" }])}
        >
          <PlusIcon /> {t("oidc.mappingAdd")}
        </Button>
      </fieldset>
      <div className="flex flex-wrap gap-4 text-sm">
        <SwitchRow
          label={t("oidc.autoCreate")}
          checked={autoCreate} onCheckedChange={setAutoCreate}
        />
        <SwitchRow
          label={t("oidc.linkEmail")}
          checked={linkEmail} onCheckedChange={setLinkEmail}
        />
        <SwitchRow
          label={t("oidc.enabled")}
          checked={enabled} onCheckedChange={setEnabled}
        />
        <SwitchRow
          label={t("oidc.trustMfa")}
          checked={trustsMfa} onCheckedChange={setTrustsMfa}
        />
      </div>
      {(localError || mut.isError) && (
        <p role="alert" className="text-sm text-destructive">
          {localError ?? errText(mut.error, t("oidc.saveError"))}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="button" disabled={mut.isPending} onClick={submit}>
          {provider ? t("oidc.save") : t("oidc.create")}
        </Button>
        <Button type="button" variant="outline" onClick={onDone}>
          {t("common.cancel")}
        </Button>
      </div>
    </div>
  );
}
