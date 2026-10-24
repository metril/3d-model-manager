/** Paginated notification delivery log with a status filter. */
import { useState } from "react";

import { useNotificationLog } from "@/api/notifications";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const PAGE = 50;
const STATUSES = ["sent", "failed", "queued", "digested", "quiet"];

export function LogViewer() {
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const log = useNotificationLog({ status, limit: PAGE, offset });
  const total = log.data?.total ?? 0;
  const items = log.data?.items ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Delivery log</CardTitle>
        <CardDescription>Recent notification attempts.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-2">
          <Label htmlFor="nl-status">Status</Label>
          <select
            id="nl-status"
            className="h-9 rounded-md border border-input bg-transparent px-3 text-sm"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">All</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        {log.isError ? (
          <p role="alert" className="text-sm text-destructive">
            Couldn&apos;t load the log.
          </p>
        ) : items.length === 0 && !log.isLoading ? (
          <p className="text-sm text-muted-foreground">No log entries.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>Channel</TableHead>
                <TableHead>Event</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Error</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((it) => (
                <TableRow key={it.id}>
                  <TableCell>{new Date(it.sent_at).toLocaleString()}</TableCell>
                  <TableCell>{it.channel_name ?? "—"}</TableCell>
                  <TableCell>{it.event}</TableCell>
                  <TableCell>{it.status}</TableCell>
                  <TableCell className="text-destructive">{it.error ?? ""}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <div className="flex items-center gap-2 text-sm">
          <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            Previous
          </Button>
          <span className="text-muted-foreground">
            {total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE, total)} of {total}
          </span>
          <Button size="sm" variant="outline" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
            Next
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
