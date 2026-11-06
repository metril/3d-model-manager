/**
 * Scoped API keys for the `/api/ext/v2` remote-control surface (backend
 * `app/api/ext_v2.py`): create (the secret is shown once), list, revoke, and
 * a docs panel with curl examples for every endpoint.
 */
import { useState } from "react";
import { CheckIcon, CopyIcon, TerminalIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import {
  useApiKeys,
  useCreateApiKey,
  useRevokeApiKey,
  type ApiKeyOut,
  type ApiKeyScope,
} from "@/api/apiKeys";
import { usePrinters } from "@/api/printers";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ENDPOINTS, curlFor } from "@/components/settings/apiKeysDocs";
import { formatDate } from "@/lib/format";

const SCOPES: ApiKeyScope[] = ["read", "control", "queue", "upload"];

const EXPIRY_OPTIONS: { value: string; labelKey: string }[] = [
  { value: "", labelKey: "apiKeys.expiry.never" },
  { value: "30", labelKey: "apiKeys.expiry.d30" },
  { value: "90", labelKey: "apiKeys.expiry.d90" },
  { value: "365", labelKey: "apiKeys.expiry.y1" },
];

export function ApiKeysCard() {
  const { t } = useTranslation();
  const keys = useApiKeys();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("apiKeys.title")}</CardTitle>
        <CardDescription>
          Scoped keys for scripts, Home Assistant and other automation. A key acts as you, can
          never exceed your own permissions, and never has admin rights. Send it as{" "}
          <code>X-API-Key</code> (or a Bearer token) to <code>/api/ext/v2</code>.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <CreateForm />
        {keys.isLoading ? (
          <Skeleton className="h-16 w-full rounded-lg" />
        ) : keys.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {keys.error instanceof ApiError ? keys.error.detail : t("apiKeys.loadError")}
          </p>
        ) : (
          <KeyList keys={keys.data ?? []} />
        )}
        <ApiDocs />
      </CardContent>
    </Card>
  );
}

function CreateForm() {
  const { t } = useTranslation();
  const [label, setLabel] = useState("");
  const [scopes, setScopes] = useState<ApiKeyScope[]>(["read"]);
  const [printerIds, setPrinterIds] = useState<number[]>([]);
  const [expiry, setExpiry] = useState("");
  const [rate, setRate] = useState("");
  const [minted, setMinted] = useState<{ label: string; key: string } | null>(null);
  const create = useCreateApiKey();
  const printers = usePrinters();

  function toggle<T>(list: T[], value: T): T[] {
    return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
  }

  function submit() {
    create.mutate(
      {
        label: label.trim(),
        scopes,
        printer_ids: printerIds.length ? printerIds : null,
        expires_in_days: expiry ? Number(expiry) : null,
        rate_limit_per_min: rate ? Number(rate) : null,
      },
      {
        onSuccess: (r) => {
          setMinted({ label: r.label, key: r.key });
          setLabel("");
          setRate("");
        },
      },
    );
  }

  if (minted) {
    return <MintedKey label={minted.label} secret={minted.key} onDismiss={() => setMinted(null)} />;
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="api-key-label">{t("apiKeys.keyLabel")}</Label>
        <Input
          id="api-key-label"
          placeholder={t("apiKeys.labelPlaceholder")}
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          disabled={create.isPending}
        />
      </div>
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">{t("apiKeys.scopes")}</legend>
        {SCOPES.map((s) => (
          <div key={s} className="flex items-start gap-2">
            <Checkbox
              id={`api-key-scope-${s}`}
              checked={scopes.includes(s)}
              onCheckedChange={() => setScopes((cur) => toggle(cur, s))}
            />
            <Label htmlFor={`api-key-scope-${s}`} className="flex flex-col items-start gap-0">
              <span>{t(`apiKeys.scope.${s}.label`)}</span>
              <span className="text-xs font-normal text-muted-foreground">{t(`apiKeys.scope.${s}.hint`)}</span>
            </Label>
          </div>
        ))}
      </fieldset>
      {printers.data && printers.data.length > 0 && (
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">{t("apiKeys.printerAllowlist")}</legend>
          <p className="text-xs text-muted-foreground">{t("apiKeys.allowlistHint")}</p>
          {printers.data.map((p) => (
            <div key={p.id} className="flex items-center gap-2">
              <Checkbox
                id={`api-key-printer-${p.id}`}
                checked={printerIds.includes(p.id)}
                onCheckedChange={() => setPrinterIds((cur) => toggle(cur, p.id))}
              />
              <Label htmlFor={`api-key-printer-${p.id}`}>{p.name}</Label>
            </div>
          ))}
        </fieldset>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="api-key-expiry">{t("apiKeys.expires")}</Label>
          <select
            id="api-key-expiry"
            className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm"
            value={expiry}
            onChange={(e) => setExpiry(e.target.value)}
          >
            {EXPIRY_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {t(o.labelKey)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="api-key-rate">{t("apiKeys.rateLimit")}</Label>
          <Input
            id="api-key-rate"
            type="number"
            min={1}
            max={6000}
            placeholder="120"
            value={rate}
            onChange={(e) => setRate(e.target.value)}
          />
        </div>
      </div>
      <Button type="button" disabled={create.isPending || !label.trim() || scopes.length === 0} onClick={submit}>
        {create.isPending ? t("apiKeys.creating") : t("apiKeys.create")}
      </Button>
      {create.isError && (
        <p role="alert" className="text-sm text-destructive">
          {create.error instanceof ApiError ? create.error.detail : t("apiKeys.createError")}
        </p>
      )}
    </div>
  );
}

function MintedKey({ label, secret, onDismiss }: { label: string; secret: string; onDismiss: () => void }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState<"key" | "curl" | null>(null);
  function copy(what: "key" | "curl") {
    const text =
      what === "key" ? secret : `curl -H "X-API-Key: ${secret}" "${window.location.origin}/api/ext/v2/printers"`;
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(what);
      window.setTimeout(() => setCopied(null), 1500);
    });
  }
  return (
    <div className="space-y-2 rounded-lg border border-warning/40 bg-warning/10 p-3">
      <p className="text-sm font-medium text-foreground">{t("apiKeys.created", { label })}</p>
      <p className="text-xs text-warning">
        {t("apiKeys.copyNow")}
      </p>
      <div className="flex items-center gap-2">
        <code aria-label={t("apiKeys.newKey")} className="min-w-0 flex-1 truncate rounded bg-muted px-2 py-1.5 font-mono text-xs">
          {secret}
        </code>
        <Button type="button" size="sm" variant="outline" onClick={() => copy("key")}>
          {copied === "key" ? (
            <>
              <CheckIcon /> {t("apiKeys.copied")}
            </>
          ) : (
            <>
              <CopyIcon /> {t("apiKeys.copy")}
            </>
          )}
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={() => copy("curl")}>
          {copied === "curl" ? (
            <>
              <CheckIcon /> {t("apiKeys.copiedCurl")}
            </>
          ) : (
            <>
              <TerminalIcon /> {t("apiKeys.copyCurl")}
            </>
          )}
        </Button>
      </div>
      <Button type="button" size="sm" variant="ghost" onClick={onDismiss}>
        {t("apiKeys.done")}
      </Button>
    </div>
  );
}

function KeyList({ keys }: { keys: ApiKeyOut[] }) {
  const { t } = useTranslation();
  if (keys.length === 0) return <p className="text-sm text-muted-foreground">{t("apiKeys.none")}</p>;
  return (
    <ul className="divide-y divide-border">
      {keys.map((k) => (
        <KeyRow key={k.id} apiKey={k} />
      ))}
    </ul>
  );
}

function KeyRow({ apiKey }: { apiKey: ApiKeyOut }) {
  const { t } = useTranslation();
  const [confirming, setConfirming] = useState(false);
  const revoke = useRevokeApiKey();
  const pending = revoke.isPending && revoke.variables === apiKey.id;
  const failed = revoke.isError && revoke.variables === apiKey.id;
  const expired = apiKey.expires_at !== null && new Date(apiKey.expires_at) <= new Date();
  return (
    <li className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <p className="text-sm font-medium text-foreground">
          {apiKey.label}{" "}
          {apiKey.key_prefix && <code className="text-xs text-muted-foreground">{apiKey.key_prefix}…</code>}
        </p>
        <p className="text-xs text-muted-foreground">
          {apiKey.scopes.join(", ")}
          {" · "}
          {apiKey.printer_ids ? t("apiKeys.printersCount", { n: apiKey.printer_ids.length }) : t("apiKeys.allPrinters")} ·{" "}
          {apiKey.rate_limit_per_min}/min
        </p>
        <p className="text-xs text-muted-foreground">
          {t("apiKeys.created_at", { date: formatDate(apiKey.created_at) })} ·{" "}
          {apiKey.last_used_at ? t("apiKeys.lastUsed", { date: formatDate(apiKey.last_used_at) }) : t("apiKeys.neverUsed")} ·{" "}
          {apiKey.expires_at
            ? expired
              ? t("apiKeys.expired", { date: formatDate(apiKey.expires_at) })
              : t("apiKeys.expiresOn", { date: formatDate(apiKey.expires_at) })
            : t("apiKeys.noExpiry")}
        </p>
        {failed && (
          <p role="alert" className="mt-1 text-xs text-destructive">
            {revoke.error instanceof ApiError ? revoke.error.detail : t("apiKeys.revokeError")}
          </p>
        )}
      </div>
      <Button type="button" size="sm" variant="outline" disabled={pending} onClick={() => setConfirming(true)}>
        {pending ? t("apiKeys.revoking") : t("apiKeys.revoke")}
      </Button>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={t("apiKeys.revokeTitle", { label: apiKey.label })}
        description={t("apiKeys.revokeDesc")}
        confirmLabel={t("apiKeys.revoke")}
        cancelLabel={t("common.cancel")}
        destructive
        onConfirm={() => revoke.mutate(apiKey.id)}
      />
    </li>
  );
}

function ApiDocs() {
  const { t } = useTranslation();
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  return (
    <details className="rounded-lg border border-border p-3">
      <summary className="cursor-pointer text-sm font-medium">{t("apiKeys.reference")}</summary>
      <div className="mt-3 space-y-3 text-xs text-muted-foreground">
        <p>
          Auth: <code>X-API-Key: &lt;key&gt;</code> or <code>Authorization: Bearer &lt;key&gt;</code>. Every
          mutating call is audit-logged. Over the key&apos;s rate limit you get <code>429</code> with{" "}
          <code>Retry-After</code>. Control commands use the same validation as the app: they return{" "}
          <code>409</code> while printing (where refused), <code>422</code> for unsupported or invalid
          arguments, and <code>202</code> if the printer has not answered yet.
        </p>
        <ul className="space-y-3">
          {ENDPOINTS.map((e) => (
            <li key={`${e.method} ${e.path}`}>
              <p className="text-foreground">
                <span className="font-mono font-semibold">{e.method}</span>{" "}
                <span className="font-mono">/api/ext/v2{e.path.split("?")[0]}</span>{" "}
                <span className="rounded bg-muted px-1">{e.scope}</span> — {e.desc}
              </p>
              <pre className="mt-1 overflow-x-auto rounded bg-muted p-2 font-mono">{curlFor(e, origin)}</pre>
            </li>
          ))}
        </ul>
        <p>
          Outgoing events: add a <strong>Webhook</strong> channel under Notifications, choose its events, and set a
          signing secret. Each delivery carries <code>X-TDMM-Timestamp</code> and{" "}
          <code>X-TDMM-Signature: sha256=HMAC_SHA256(secret, timestamp + &quot;.&quot; + body)</code>.
        </p>
      </div>
    </details>
  );
}
