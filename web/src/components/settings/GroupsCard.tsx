/** Admin: groups with permissions, members and the per-printer access matrix. */
import { useState } from "react";

import {
  useAdminGroups,
  useAdminUsers,
  useCreateGroup,
  useDeleteGroup,
  usePatchGroup,
  useSetGroupMembers,
  useSetGroupPrinterAccess,
} from "@/api/admin";
import { ApiError } from "@/api/client";
import { usePrinters } from "@/api/printers";
import type { GroupOut, PrinterAccessIO } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

const PERMS = [
  "admin",
  "library:write",
  "inventory:write",
  "printers:print",
  "printers:control",
  "queue:review",
  "settings:write",
  "billing:manage",
];

type Flag = "can_print" | "can_control" | "requires_review";
const FLAGS: { key: Flag; label: string }[] = [
  { key: "can_print", label: "Print" },
  { key: "can_control", label: "Control" },
  { key: "requires_review", label: "Requires review" },
];

function errText(e: unknown, fallback: string) {
  return e instanceof ApiError ? e.detail : fallback;
}

export function GroupsCard() {
  const groups = useAdminGroups();
  const [editing, setEditing] = useState<number | null>(null);
  const del = useDeleteGroup();
  const [error, setError] = useState<string | null>(null);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Groups</CardTitle>
        <CardDescription>Grant permissions and printer access to sets of users.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <CreateGroupForm />
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        {groups.isLoading ? (
          <Skeleton className="h-16 w-full rounded-lg" />
        ) : groups.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {errText(groups.error, "Couldn't load groups.")}
          </p>
        ) : (groups.data ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">No groups yet.</p>
        ) : (
          <ul className="divide-y">
            {(groups.data ?? []).map((g) => (
              <li key={g.id} className="space-y-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{g.name}</span>
                  <span className="text-sm text-muted-foreground">{g.members.length} members</span>
                  <div className="ml-auto flex gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      aria-label={`Edit ${g.name}`}
                      onClick={() => setEditing(editing === g.id ? null : g.id)}
                    >
                      {editing === g.id ? "Close" : "Edit"}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="destructive"
                      aria-label={`Delete ${g.name}`}
                      onClick={() => {
                        setError(null);
                        del.mutate(g.id, { onError: (e) => setError(errText(e, "Could not delete the group.")) });
                      }}
                    >
                      Delete
                    </Button>
                  </div>
                </div>
                {editing === g.id && <GroupEditor group={g} />}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function CreateGroupForm() {
  const [name, setName] = useState("");
  const create = useCreateGroup();
  return (
    <div className="space-y-2">
      <div className="flex items-end gap-3">
        <div className="flex flex-1 flex-col gap-1.5">
          <Label htmlFor="new-group-name">Group name</Label>
          <Input id="new-group-name" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <Button
          type="button"
          disabled={create.isPending || !name.trim()}
          onClick={() => create.mutate({ name: name.trim() }, { onSuccess: () => setName("") })}
        >
          Add group
        </Button>
      </div>
      {create.isError && (
        <p role="alert" className="text-sm text-destructive">
          {errText(create.error, "Could not create the group.")}
        </p>
      )}
    </div>
  );
}

function toggle<T>(list: T[], item: T, on: boolean): T[] {
  return on ? [...list.filter((x) => x !== item), item] : list.filter((x) => x !== item);
}

function GroupEditor({ group }: { group: GroupOut }) {
  const users = useAdminUsers();
  const printers = usePrinters();
  const patch = usePatchGroup();
  const setMembers = useSetGroupMembers();
  const setAccess = useSetGroupPrinterAccess();

  const [name, setName] = useState(group.name);
  const [perms, setPerms] = useState(group.permissions);
  const [memberIds, setMemberIds] = useState(group.members.map((m) => m.user_id));
  const [access, setAccess_] = useState<Record<number, PrinterAccessIO>>(
    Object.fromEntries(group.printer_access.map((r) => [r.printer_id, r])),
  );

  const rowFor = (printerId: number): PrinterAccessIO =>
    access[printerId] ?? { printer_id: printerId, can_print: false, can_control: false, requires_review: false };

  function saveAccess() {
    // Full set; rows with every flag false are omitted (the server drops them).
    const rows = Object.values(access).filter((r) => r.can_print || r.can_control || r.requires_review);
    setAccess.mutate({ id: group.id, rows });
  }

  const err = (m: { isError: boolean; error: unknown }, fb: string) =>
    m.isError ? (
      <p role="alert" className="text-sm text-destructive">
        {errText(m.error, fb)}
      </p>
    ) : null;

  return (
    <div className="space-y-5 rounded-lg border p-3">
      <section className="space-y-2">
        <Label htmlFor={`group-name-${group.id}`}>Name</Label>
        <Input id={`group-name-${group.id}`} value={name} onChange={(e) => setName(e.target.value)} />
        <div className="grid gap-2 sm:grid-cols-2">
          {PERMS.map((p) => (
            <label key={p} className="flex items-center gap-2 text-sm">
              <Checkbox checked={perms.includes(p)} onCheckedChange={(c) => setPerms(toggle(perms, p, c === true))} />
              {p}
            </label>
          ))}
        </div>
        <Button
          type="button"
          size="sm"
          disabled={patch.isPending || !name.trim()}
          onClick={() => patch.mutate({ id: group.id, body: { name: name.trim(), permissions: perms } })}
        >
          Save details
        </Button>
        {err(patch, "Could not save the group.")}
      </section>

      <section className="space-y-2">
        <h4 className="text-sm font-medium">Members</h4>
        <div className="grid gap-2 sm:grid-cols-2">
          {(users.data ?? []).map((u) => (
            <label key={u.id} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={memberIds.includes(u.id)}
                onCheckedChange={(c) => setMemberIds(toggle(memberIds, u.id, c === true))}
              />
              {u.username}
            </label>
          ))}
        </div>
        <Button
          type="button"
          size="sm"
          disabled={setMembers.isPending}
          onClick={() => setMembers.mutate({ id: group.id, user_ids: memberIds })}
        >
          Save members
        </Button>
        {err(setMembers, "Could not save the members.")}
      </section>

      <section className="space-y-2">
        <h4 className="text-sm font-medium">Printer access</h4>
        {(printers.data ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">No printers configured.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th className="py-1 font-medium">Printer</th>
                {FLAGS.map((f) => (
                  <th key={f.key} className="py-1 font-medium">
                    {f.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(printers.data ?? []).map((pr) => (
                <tr key={pr.id}>
                  <td className="py-1">{pr.name}</td>
                  {FLAGS.map((f) => (
                    <td key={f.key} className="py-1">
                      <Checkbox
                        aria-label={`${f.label} on ${pr.name}`}
                        checked={rowFor(pr.id)[f.key]}
                        onCheckedChange={(c) =>
                          setAccess_({ ...access, [pr.id]: { ...rowFor(pr.id), [f.key]: c === true } })
                        }
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <Button type="button" size="sm" disabled={setAccess.isPending} onClick={saveAccess}>
          Save printer access
        </Button>
        {err(setAccess, "Could not save printer access.")}
      </section>
    </div>
  );
}
