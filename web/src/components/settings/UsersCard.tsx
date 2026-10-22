/** Admin: list, create, edit, reset password for, and deactivate users. */
import { useState } from "react";

import {
  useAdminUsers,
  useCreateUser,
  useDeleteUser,
  usePatchUser,
  useResetUserPassword,
} from "@/api/admin";
import { ApiError } from "@/api/client";
import type { UserAdminOut, UserRole } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

const ROLES: UserRole[] = ["admin", "member", "viewer"];

function errText(e: unknown, fallback: string) {
  return e instanceof ApiError ? e.detail : fallback;
}

function RoleSelect({
  id,
  label,
  value,
  onChange,
}: {
  id?: string;
  label: string;
  value: UserRole;
  onChange: (r: UserRole) => void;
}) {
  return (
    <select
      id={id}
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value as UserRole)}
      className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm"
    >
      {ROLES.map((r) => (
        <option key={r} value={r}>
          {r}
        </option>
      ))}
    </select>
  );
}

export function UsersCard() {
  const users = useAdminUsers();
  const [error, setError] = useState<string | null>(null);
  const patch = usePatchUser();
  const del = useDeleteUser();

  function run(p: Promise<unknown>, fallback: string) {
    setError(null);
    p.catch((e) => setError(errText(e, fallback)));
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Users</CardTitle>
        <CardDescription>Accounts, roles, and password resets.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <CreateUserForm />
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        {users.isLoading ? (
          <Skeleton className="h-16 w-full rounded-lg" />
        ) : users.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {errText(users.error, "Couldn't load users.")}
          </p>
        ) : (
          <ul className="divide-y">
            {(users.data ?? []).map((u) => (
              <UserRow
                key={u.id}
                user={u}
                onRole={(role) => run(patch.mutateAsync({ id: u.id, body: { role } }), "Could not change the role.")}
                onActive={(is_active) =>
                  run(patch.mutateAsync({ id: u.id, body: { is_active } }), "Could not update the account.")
                }
                onDelete={() => run(del.mutateAsync(u.id), "Could not delete the user.")}
                onError={setError}
              />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function UserRow({
  user,
  onRole,
  onActive,
  onDelete,
  onError,
}: {
  user: UserAdminOut;
  onRole: (r: UserRole) => void;
  onActive: (a: boolean) => void;
  onDelete: () => void;
  onError: (m: string | null) => void;
}) {
  const [pw, setPw] = useState("");
  const [open, setOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const reset = useResetUserPassword();

  function submitReset() {
    onError(null);
    reset.mutate(
      { id: user.id, new_password: pw },
      {
        onSuccess: () => {
          setPw("");
          setOpen(false);
        },
        onError: (e) => onError(errText(e, "Could not reset the password.")),
      },
    );
  }

  return (
    <li className="space-y-2 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{user.username}</span>
        {user.display_name && <span className="text-sm text-muted-foreground">{user.display_name}</span>}
        {user.oidc_linked && <Badge variant="secondary">SSO</Badge>}
        {!user.is_active && <Badge variant="outline">Inactive</Badge>}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <RoleSelect label={`Role for ${user.username}`} value={user.role} onChange={onRole} />
          <Button type="button" size="sm" variant="outline" onClick={() => setOpen((o) => !o)}>
            Reset password
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={() => onActive(!user.is_active)}>
            {user.is_active ? "Deactivate" : "Activate"}
          </Button>
          {confirmDelete ? (
            <>
              <Button type="button" size="sm" variant="destructive" onClick={onDelete}>
                Confirm delete
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={() => setConfirmDelete(false)}>
                Cancel
              </Button>
            </>
          ) : (
            <Button type="button" size="sm" variant="destructive" onClick={() => setConfirmDelete(true)}>
              Delete
            </Button>
          )}
        </div>
      </div>
      {open && (
        <div className="flex items-end gap-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`reset-pw-${user.id}`}>New password for {user.username}</Label>
            <Input
              id={`reset-pw-${user.id}`}
              type="password"
              value={pw}
              onChange={(e) => setPw(e.target.value)}
              autoComplete="new-password"
            />
          </div>
          <Button type="button" size="sm" disabled={pw.length < 8 || reset.isPending} onClick={submitReset}>
            Set password
          </Button>
        </div>
      )}
    </li>
  );
}

function CreateUserForm() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<UserRole>("member");
  const create = useCreateUser();

  function submit() {
    create.mutate(
      { username: username.trim(), password: password || null, role },
      {
        onSuccess: () => {
          setUsername("");
          setPassword("");
          setRole("member");
        },
      },
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="flex flex-1 flex-col gap-1.5">
          <Label htmlFor="new-user-name">Username</Label>
          <Input id="new-user-name" value={username} onChange={(e) => setUsername(e.target.value)} />
        </div>
        <div className="flex flex-1 flex-col gap-1.5">
          <Label htmlFor="new-user-password">Password (blank for SSO-only)</Label>
          <Input
            id="new-user-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="new-user-role">Role</Label>
          <RoleSelect id="new-user-role" label="Role" value={role} onChange={setRole} />
        </div>
        <Button type="button" disabled={create.isPending || !username.trim()} onClick={submit}>
          Add user
        </Button>
      </div>
      {create.isError && (
        <p role="alert" className="text-sm text-destructive">
          {errText(create.error, "Could not create the user.")}
        </p>
      )}
    </div>
  );
}
