import { useState } from "react";

import { ApiError } from "@/api/client";
import { useImportTokens, useUpdateImportTokens } from "@/api/imports";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

export function SiteTokensCard() {
  const tokens = useImportTokens();
  const update = useUpdateImportTokens();
  const [thingiverseToken, setThingiverseToken] = useState("");
  const [makerworldToken, setMakerworldToken] = useState("");

  if (tokens.isLoading) return <Skeleton className="h-40 w-full rounded-xl" />;
  const isThingiverseSet = tokens.data?.thingiverse_token === "***";
  const isMakerworldSet = tokens.data?.makerworld_token === "***";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Gallery site tokens</CardTitle>
        <CardDescription>
          Thingiverse needs a personal App Token to download files. Create a &quot;Desktop app&quot; at
          thingiverse.com/apps/create and paste the token here. Printables downloads still need no
          token, but connecting a Printables account below unlocks syncing your saved collections.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="thingiverse-token">Thingiverse App Token</Label>
          <Input
            id="thingiverse-token"
            type="password"
            autoComplete="new-password"
            value={thingiverseToken}
            placeholder={isThingiverseSet ? "•• (stored — leave blank to keep)" : "paste your app token"}
            onChange={(e) => setThingiverseToken(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="makerworld-token">MakerWorld web token</Label>
          <Input
            id="makerworld-token"
            type="password"
            autoComplete="new-password"
            value={makerworldToken}
            placeholder={isMakerworldSet ? "•• (stored — leave blank to keep)" : "paste your web token"}
            onChange={(e) => setMakerworldToken(e.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            DevTools → Application → Cookies → https://makerworld.com → copy the value of{" "}
            <code>token</code>. Needed to sync your MakerWorld collections; it expires periodically
            and can be re-pasted.
          </p>
        </div>
        <Button
          type="button"
          disabled={update.isPending}
          onClick={() =>
            update.mutate(
              { thingiverse_token: thingiverseToken, makerworld_token: makerworldToken },
              {
                onSuccess: () => {
                  setThingiverseToken("");
                  setMakerworldToken("");
                },
              },
            )
          }
        >
          {update.isPending ? "Saving…" : "Save tokens"}
        </Button>
        {update.isError && (
          <p role="alert" className="text-sm text-destructive">
            {update.error instanceof ApiError ? update.error.detail : "Could not save the tokens."}
          </p>
        )}
        {update.isSuccess && <p className="text-sm text-success">Saved.</p>}
      </CardContent>
    </Card>
  );
}
