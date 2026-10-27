import { useEffect, useState } from "react";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useBackups, useOpsSettings, useRestoreSettings, useRunBackup, useSaveOpsSettings } from "@/api/ops";
import type { BackupRun, GitProvider } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

const errText = (e: unknown, fb: string) => (e instanceof ApiError ? e.detail : fb);
const NONE = "none";

function fmtSize(n: number | null) {
  if (n == null) return "—";
  return n > 1_048_576 ? `${(n / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

function RunRow({ run, isAdmin }: { run: BackupRun; isAdmin: boolean }) {
  const restore = useRestoreSettings();
  return (
    <li className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-sm">
      <span className="font-medium">{run.status}</span>
      <span className="text-muted-foreground">
        {run.kind} · {new Date(run.created_at).toLocaleString()} · {fmtSize(run.size_bytes)}
        {run.git_pushed ? " · pushed" : ""}
      </span>
      {run.location ? <span className="truncate text-xs text-muted-foreground">{run.location}</span> : null}
      {run.error ? <span className="text-xs text-destructive">{run.error}</span> : null}
      {isAdmin && run.status === "ok" ? (
        <ConfirmDialog
          trigger={
            <Button type="button" size="sm" variant="outline" className="ml-auto">
              Restore settings
            </Button>
          }
          title="Restore settings from this backup?"
          description="Overwrites current app settings with the values in this backup."
          confirmLabel="Restore"
          destructive
          onConfirm={() => restore.mutate(run.id)}
        />
      ) : null}
      {restore.isSuccess ? <span className="w-full text-xs text-success">Restored {restore.data.restored} settings.</span> : null}
      {restore.isError ? (
        <span role="alert" className="w-full text-xs text-destructive">
          {errText(restore.error, "Restore failed")}
        </span>
      ) : null}
    </li>
  );
}

export function BackupsCard() {
  const auth = useAuth();
  const isAdmin = auth.can("admin");
  const ops = useOpsSettings();
  const runs = useBackups(isAdmin);
  const save = useSaveOpsSettings();
  const run = useRunBackup();
  const [enabled, setEnabled] = useState(false);
  const [interval, setIntervalH] = useState("24");
  const [provider, setProvider] = useState<string>(NONE);
  const [baseUrl, setBaseUrl] = useState("");
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState("main");
  const [token, setToken] = useState("");

  const d = ops.data;
  useEffect(() => {
    if (!d) return;
    setEnabled(d.backup_enabled);
    setIntervalH(String(d.backup_interval_hours));
    setProvider(d.git_provider ?? NONE);
    setBaseUrl(d.git_base_url ?? "");
    setRepo(d.git_repo ?? "");
    setBranch(d.git_branch);
  }, [d]);

  function onSave() {
    save.mutate(
      {
        backup_enabled: enabled,
        backup_interval_hours: Number(interval) || 24,
        git_provider: provider === NONE ? null : (provider as GitProvider),
        git_base_url: baseUrl.trim() || null,
        git_repo: repo.trim() || null,
        git_branch: branch.trim() || "main",
        // Blank = keep stored token (omit); never echo it back.
        ...(token ? { git_token: token } : {}),
      },
      { onSuccess: () => setToken("") },
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Backups</CardTitle>
        <CardDescription>Scheduled database + settings backups, optionally pushed to a git repository.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {ops.isLoading ? <p className="text-sm text-muted-foreground">Loading...</p> : null}
        {ops.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {errText(ops.error, "Couldn't load backup settings")}
          </p>
        ) : null}
        <div className="flex items-center gap-3">
          <Switch id="bk-enabled" checked={enabled} onCheckedChange={setEnabled} />
          <Label htmlFor="bk-enabled">Scheduled backups</Label>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="bk-interval">Interval (hours)</Label>
            <Input id="bk-interval" type="number" min="1" value={interval} onChange={(e) => setIntervalH(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="bk-provider">Git provider</Label>
            <Select value={provider} onValueChange={setProvider}>
              <SelectTrigger id="bk-provider" aria-label="Git provider">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>None</SelectItem>
                <SelectItem value="github">GitHub</SelectItem>
                <SelectItem value="gitlab">GitLab</SelectItem>
                <SelectItem value="gitea">Gitea</SelectItem>
                <SelectItem value="forgejo">Forgejo</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {provider !== NONE ? (
            <>
              {provider === "gitea" || provider === "forgejo" || provider === "gitlab" ? (
                <div className="space-y-1.5">
                  <Label htmlFor="bk-base">Base URL</Label>
                  <Input id="bk-base" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
                </div>
              ) : null}
              <div className="space-y-1.5">
                <Label htmlFor="bk-repo">Repository (owner/name)</Label>
                <Input id="bk-repo" value={repo} onChange={(e) => setRepo(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="bk-branch">Branch</Label>
                <Input id="bk-branch" value={branch} onChange={(e) => setBranch(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="bk-token">Access token</Label>
                <Input
                  id="bk-token"
                  type="password"
                  autoComplete="off"
                  placeholder={d?.git_token_set ? "Stored — leave blank to keep" : ""}
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                />
              </div>
            </>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" onClick={onSave} disabled={save.isPending || !d}>
            {save.isPending ? "Saving..." : "Save"}
          </Button>
          {isAdmin ? (
            <Button type="button" variant="outline" onClick={() => run.mutate()} disabled={run.isPending}>
              Run backup now
            </Button>
          ) : null}
          {save.isSuccess ? <span className="text-sm text-success">Saved.</span> : null}
        </div>
        {save.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {errText(save.error, "Could not save")}
          </p>
        ) : null}
        {run.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {errText(run.error, "Could not start backup")}
          </p>
        ) : null}
        {isAdmin ? (
          runs.data?.length ? (
            <ul className="space-y-2">
              {runs.data.map((r) => (
                <RunRow key={r.id} run={r} isAdmin={isAdmin} />
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No backups yet.</p>
          )
        ) : null}
      </CardContent>
    </Card>
  );
}
