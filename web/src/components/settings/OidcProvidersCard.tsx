/** Admin: OIDC (SSO) providers. The client secret is write-only. */
import { useState } from "react";
import { CheckIcon, CopyIcon } from "lucide-react";

import {
  useAdminOidcProviders,
  useCreateOidcProvider,
  useDeleteOidcProvider,
  usePatchOidcProvider,
} from "@/api/admin";
import { ApiError } from "@/api/client";
import type { OidcProviderOut } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

function errText(e: unknown, fallback: string) {
  return e instanceof ApiError ? e.detail : fallback;
}

export function OidcProvidersCard() {
  const providers = useAdminOidcProviders();
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const del = useDeleteOidcProvider();

  return (
    <Card>
      <CardHeader>
        <CardTitle>SSO providers</CardTitle>
        <CardDescription>OpenID Connect providers users can sign in with.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {providers.isLoading ? (
          <Skeleton className="h-16 w-full rounded-lg" />
        ) : providers.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {errText(providers.error, "Couldn't load providers.")}
          </p>
        ) : (
          <ul className="divide-y">
            {(providers.data ?? []).map((p) => (
              <li key={p.id} className="space-y-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{p.name}</span>
                  {!p.enabled && <span className="text-sm text-muted-foreground">disabled</span>}
                  <div className="ml-auto flex gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      aria-label={`Edit ${p.name}`}
                      onClick={() => setEditing(editing === p.id ? null : p.id)}
                    >
                      {editing === p.id ? "Close" : "Edit"}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="destructive"
                      aria-label={`Delete ${p.name}`}
                      onClick={() => del.mutate(p.id)}
                    >
                      Delete
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
            {errText(del.error, "Could not delete the provider.")}
          </p>
        )}
        {editing === "new" ? (
          <ProviderForm provider={null} onDone={() => setEditing(null)} />
        ) : (
          <Button type="button" variant="outline" onClick={() => setEditing("new")}>
            Add provider
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function ProviderForm({ provider, onDone }: { provider: OidcProviderOut | null; onDone: () => void }) {
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
  const [mapping, setMapping] = useState(JSON.stringify(provider?.group_mapping ?? {}));
  const [autoCreate, setAutoCreate] = useState(provider?.auto_create_users ?? false);
  const [linkEmail, setLinkEmail] = useState(provider?.link_by_email ?? false);
  const [enabled, setEnabled] = useState(provider?.enabled ?? true);
  const [trustsMfa, setTrustsMfa] = useState(provider?.trusts_idp_mfa ?? false);
  const [copied, setCopied] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const mut = provider ? patch : create;

  function submit() {
    let group_mapping: Record<string, number>;
    try {
      group_mapping = JSON.parse(mapping || "{}") as Record<string, number>;
    } catch {
      setLocalError('Group mapping must be JSON, e.g. {"staff": 3} (claim value to group id).');
      return;
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
          <span className="text-muted-foreground">Callback URL</span>
          <code className="break-all">{provider.callback_url}</code>
          <Button type="button" size="icon-sm" variant="ghost" aria-label="Copy callback URL" onClick={copy}>
            {copied ? <CheckIcon /> : <CopyIcon />}
          </Button>
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        {field("oidc-name", "Name", name, setName)}
        {field("oidc-slug", "Slug", slug, setSlug)}
        {field("oidc-discovery", "Discovery URL", discovery, setDiscovery)}
        {field("oidc-client-id", "Client ID", clientId, setClientId)}
        {field("oidc-secret", "Client secret", secret, setSecret, {
          type: "password",
          autoComplete: "new-password",
          placeholder: provider?.has_client_secret ? "Leave blank to keep current secret" : "",
        })}
        {field("oidc-scopes", "Scopes", scopes, setScopes)}
        {field("oidc-groups-claim", "Groups claim", groupsClaim, setGroupsClaim)}
        {field("oidc-admin-group", "Admin group", adminGroup, setAdminGroup)}
      </div>
      {field("oidc-mapping", "Group mapping (JSON: claim value to group id)", mapping, setMapping)}
      <div className="flex flex-wrap gap-4 text-sm">
        <label className="flex items-center gap-2">
          <Switch checked={autoCreate} onCheckedChange={setAutoCreate} aria-label="Auto-create users" />
          Auto-create users
        </label>
        <label className="flex items-center gap-2">
          <Switch checked={linkEmail} onCheckedChange={setLinkEmail} aria-label="Link by email" />
          Link by email
        </label>
        <label className="flex items-center gap-2">
          <Switch checked={enabled} onCheckedChange={setEnabled} aria-label="Enabled" />
          Enabled
        </label>
        <label className="flex items-center gap-2">
          <Switch checked={trustsMfa} onCheckedChange={setTrustsMfa} aria-label="Trust identity provider MFA" />
          Trust identity provider MFA (skip app 2FA)
        </label>
      </div>
      {(localError || mut.isError) && (
        <p role="alert" className="text-sm text-destructive">
          {localError ?? errText(mut.error, "Could not save the provider.")}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="button" disabled={mut.isPending} onClick={submit}>
          {provider ? "Save provider" : "Create provider"}
        </Button>
        <Button type="button" variant="outline" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
