import { useAuth } from "@/api/auth";
import { useOpsSettings, useSaveOpsSettings, useUpdateInfo } from "@/api/ops";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { SwitchRow } from "@/components/ui/switch-row";

export function UpdatesCard() {
  const auth = useAuth();
  const isAdmin = auth.can("admin");
  const info = useUpdateInfo();
  const ops = useOpsSettings();
  const save = useSaveOpsSettings();
  const u = info.data;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Updates</CardTitle>
        <CardDescription>Checks GitHub releases once a day.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {u ? (
          <>
            <p className="text-sm">
              Current <span className="font-mono">{u.current}</span> · Latest{" "}
              <span className="font-mono">{u.latest ?? "unknown"}</span>
            </p>
            {u.update_available ? (
              <p className="text-sm font-medium text-primary">
                An update is available.{" "}
                {u.url ? (
                  <a href={u.url} target="_blank" rel="noreferrer" className="underline">
                    Release notes
                  </a>
                ) : null}
              </p>
            ) : (
              <p className="text-sm text-muted-foreground">You&apos;re up to date.</p>
            )}
            {u.error ? (
              <p role="alert" className="text-sm text-destructive">
                {u.error}
              </p>
            ) : null}
          </>
        ) : info.isError ? (
          <p role="alert" className="text-sm text-destructive">
            Couldn&apos;t check for updates.
          </p>
        ) : null}
        {ops.data ? (
          <SwitchRow
            id="upd-enabled"
            label="Check for updates"
            checked={ops.data.update_check_enabled}
            onCheckedChange={(v) => save.mutate({ update_check_enabled: v })}
          />
        ) : null}
        {isAdmin ? (
          <Button asChild variant="outline" size="sm">
            <a href="/api/admin/support-bundle" download>
              Support bundle
            </a>
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}
