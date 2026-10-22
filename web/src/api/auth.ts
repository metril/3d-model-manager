import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { can, canPrinter, type Perm } from "@/lib/permissions";

export interface PrinterFlags {
  print: boolean;
  control: boolean;
  requires_review?: boolean;
}

export interface Me {
  id: number;
  username: string;
  display_name: string | null;
  email: string | null;
  role: string;
  permissions: string[];
  groups: { id: number; name: string }[];
  has_password: boolean;
  /** null for admins (all printers), else printer id -> flags. */
  printer_access: Record<string, PrinterFlags> | null;
}

export interface OidcProvider {
  slug: string;
  name: string;
}

export interface LoginPayload {
  username: string;
  password: string;
}

/** Query key + fetcher for `GET /auth/me`, shared by `useAuth` and the
 * authenticated layout route's `beforeLoad` guard (see `src/routes.tsx`). */
export const authQueryOptions = queryOptions({
  queryKey: ["auth", "me"] as const,
  queryFn: () => api.get<Me>("/auth/me"),
  retry: false,
});

/** Bootstraps auth state from `GET /auth/me`. */
export function useAuth() {
  const query = useQuery(authQueryOptions);
  return {
    ...query,
    can: (perm: Perm) => can(query.data, perm),
    canPrinter: (id: number, kind: "print" | "control") => canPrinter(query.data, id, kind),
  };
}

/** Public list of configured SSO providers for the login page. */
export function useOidcProviders() {
  return useQuery({
    queryKey: ["auth", "oidc-providers"] as const,
    queryFn: () => api.get<OidcProvider[]>("/auth/oidc/providers"),
    retry: false,
  });
}

/** `POST /auth/login`, then invalidates the `me` query so consumers refetch. */
export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: LoginPayload) => api.post<void>("/auth/login", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: authQueryOptions.queryKey }),
  });
}

/** `POST /auth/logout`, then clears the cached `me` query. */
export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<void>("/auth/logout"),
    onSuccess: () => queryClient.setQueryData(authQueryOptions.queryKey, undefined),
  });
}

export interface ChangePasswordPayload {
  current_password: string;
  new_password: string;
}

/** `POST /auth/password` (Round 10 T4): rotates the admin's password hash
 * and deletes every OTHER session for this user server-side. The current
 * session (and its cookie) stays valid on success, so no client-side
 * session/redirect handling is needed here beyond the request itself. */
export function useChangePassword() {
  return useMutation({
    mutationFn: (payload: ChangePasswordPayload) => api.post<void>("/auth/password", payload),
  });
}
