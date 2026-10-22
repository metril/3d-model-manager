/** Admin hooks (P1): users, groups (+ members, printer access) and OIDC providers. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type {
  GroupCreateIn,
  GroupOut,
  GroupPatchIn,
  OidcProviderCreateIn,
  OidcProviderOut,
  OidcProviderPatchIn,
  PrinterAccessIO,
  UserAdminOut,
  UserCreateIn,
  UserPatchIn,
} from "@/api/types";

const USERS = ["admin", "users"] as const;
const GROUPS = ["admin", "groups"] as const;
const PROVIDERS = ["admin", "oidc-providers"] as const;

function useInvalidating<V, R>(fn: (v: V) => Promise<R>, keys: (readonly string[])[]) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      // Group membership shows on users and vice versa.
      for (const queryKey of keys) void qc.invalidateQueries({ queryKey });
    },
  });
}

export function useAdminUsers() {
  return useQuery({ queryKey: USERS, queryFn: () => api.get<UserAdminOut[]>("/admin/users") });
}
export function useCreateUser() {
  return useInvalidating((b: UserCreateIn) => api.post<UserAdminOut>("/admin/users", b), [USERS]);
}
export function usePatchUser() {
  return useInvalidating(
    ({ id, body }: { id: number; body: UserPatchIn }) => api.patch<UserAdminOut>(`/admin/users/${id}`, body),
    [USERS, GROUPS],
  );
}
export function useResetUserPassword() {
  return useInvalidating(
    ({ id, new_password }: { id: number; new_password: string }) =>
      api.post<void>(`/admin/users/${id}/password`, { new_password }),
    [USERS],
  );
}
export function useDeleteUser() {
  return useInvalidating((id: number) => api.delete<void>(`/admin/users/${id}`), [USERS, GROUPS]);
}

export function useAdminGroups() {
  return useQuery({ queryKey: GROUPS, queryFn: () => api.get<GroupOut[]>("/admin/groups") });
}
export function useCreateGroup() {
  return useInvalidating((b: GroupCreateIn) => api.post<GroupOut>("/admin/groups", b), [GROUPS]);
}
export function usePatchGroup() {
  return useInvalidating(
    ({ id, body }: { id: number; body: GroupPatchIn }) => api.patch<GroupOut>(`/admin/groups/${id}`, body),
    [GROUPS, USERS],
  );
}
export function useDeleteGroup() {
  return useInvalidating((id: number) => api.delete<void>(`/admin/groups/${id}`), [GROUPS, USERS]);
}
export function useSetGroupMembers() {
  return useInvalidating(
    ({ id, user_ids }: { id: number; user_ids: number[] }) =>
      api.put<GroupOut>(`/admin/groups/${id}/members`, { user_ids }),
    [GROUPS, USERS],
  );
}
export function useSetGroupPrinterAccess() {
  return useInvalidating(
    ({ id, rows }: { id: number; rows: PrinterAccessIO[] }) =>
      api.put<GroupOut>(`/admin/groups/${id}/printer-access`, rows),
    [GROUPS],
  );
}

export function useAdminOidcProviders() {
  return useQuery({
    queryKey: PROVIDERS,
    queryFn: () => api.get<OidcProviderOut[]>("/admin/oidc-providers"),
  });
}
export function useCreateOidcProvider() {
  return useInvalidating(
    (b: OidcProviderCreateIn) => api.post<OidcProviderOut>("/admin/oidc-providers", b),
    [PROVIDERS, ["auth", "oidc-providers"]],
  );
}
export function usePatchOidcProvider() {
  return useInvalidating(
    ({ id, body }: { id: number; body: OidcProviderPatchIn }) =>
      api.patch<OidcProviderOut>(`/admin/oidc-providers/${id}`, body),
    [PROVIDERS, ["auth", "oidc-providers"]],
  );
}
export function useDeleteOidcProvider() {
  return useInvalidating(
    (id: number) => api.delete<void>(`/admin/oidc-providers/${id}`),
    [PROVIDERS, ["auth", "oidc-providers"]],
  );
}
