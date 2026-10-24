import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { can, canPrinter, type Perm } from "@/lib/permissions";

export interface PrinterFlags {
  print: boolean;
  control: boolean;
  requires_review: boolean;
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
  /** Optional only so unrelated test fixtures stay valid; the API always sends both. */
  two_factor_enabled?: boolean;
  /** Server requires 2FA and this user has no method yet: only enrolment is allowed. */
  must_enroll_2fa?: boolean;
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

export type TwoFactorMethod = "totp" | "email" | "backup";

export type LoginResult =
  | { requires_2fa: false }
  | { requires_2fa: true; pre_auth_token: string; methods: TwoFactorMethod[] };

/** `POST /auth/login`: 204 = signed in, 200 `{requires_2fa:true,...}` = second step needed
 * (no cookie yet). Invalidates `me` only when the session was actually created. */
export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    gcTime: 0,
    mutationFn: async (payload: LoginPayload): Promise<LoginResult> => {
      const res = await api.post<LoginResult | undefined>("/auth/login", payload);
      return res && res.requires_2fa ? res : { requires_2fa: false };
    },
    onSuccess: (res) => {
      if (!res.requires_2fa) return queryClient.invalidateQueries({ queryKey: authQueryOptions.queryKey });
    },
  });
}

export interface Verify2faPayload {
  pre_auth_token: string;
  method: TwoFactorMethod;
  code: string;
}

export type Verify2faResult = Me & { return_to?: string | null };

/** `POST /auth/2fa/verify`: sets the session cookie, then refreshes `me`. */
export function useVerify2fa() {
  const queryClient = useQueryClient();
  return useMutation({
    gcTime: 0,
    mutationFn: (payload: Verify2faPayload) => api.post<Verify2faResult>("/auth/2fa/verify", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: authQueryOptions.queryKey }),
  });
}

/** `POST /auth/2fa/email/send`: emails a login code (409 rate_limited / smtp_not_configured). */
export function useSend2faEmail() {
  return useMutation({
    mutationFn: (pre_auth_token: string) =>
      api.post<{ sent: boolean }>("/auth/2fa/email/send", { pre_auth_token }),
  });
}

export interface TwoFactorStatus {
  totp_enabled: boolean;
  email_otp_enabled: boolean;
  backup_codes_remaining: number;
  methods: TwoFactorMethod[];
  smtp_configured: boolean;
  email_available: boolean;
}

export const twoFactorStatusQueryOptions = queryOptions({
  queryKey: ["auth", "2fa", "status"] as const,
  queryFn: () => api.get<TwoFactorStatus>("/auth/2fa/status"),
});

export function useTwoFactorStatus() {
  return useQuery(twoFactorStatusQueryOptions);
}

/** Self-service 2FA changes alter both the status card and `me` (`two_factor_enabled`, the gate). */
function useTwoFactorMutation<TVars, TOut>(fn: (v: TVars) => Promise<TOut>) {
  const queryClient = useQueryClient();
  return useMutation({
    gcTime: 0,
    mutationFn: fn,
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: twoFactorStatusQueryOptions.queryKey }),
        queryClient.invalidateQueries({ queryKey: authQueryOptions.queryKey }),
      ]),
  });
}

export interface TotpSetup {
  secret: string;
  otpauth_url: string;
  setup_token: string;
}

export function useTotpSetup() {
  return useMutation({ gcTime: 0, mutationFn: () => api.post<TotpSetup>("/auth/2fa/totp/setup") });
}

export function useTotpEnable() {
  return useTwoFactorMutation((v: { setup_token: string; code: string }) =>
    api.post<{ backup_codes: string[] }>("/auth/2fa/totp/enable", v),
  );
}

export function useTotpDisable() {
  return useTwoFactorMutation((code_or_password: string) =>
    api.post<void>("/auth/2fa/totp/disable", { code_or_password }),
  );
}

export function useRegenerateBackup() {
  return useTwoFactorMutation((code: string) =>
    api.post<{ backup_codes: string[] }>("/auth/2fa/backup/regenerate", { code }),
  );
}

/** Emails a code so the user can re-authenticate (e.g. before disabling). */
export function useEmailOtpRequest() {
  return useMutation({ mutationFn: () => api.post<{ sent: boolean }>("/auth/2fa/email/request") });
}

/** Step 1 of enabling email codes: emails a confirm code. */
export function useEmailOtpEnable() {
  return useMutation({ mutationFn: () => api.post<{ sent: boolean }>("/auth/2fa/email/enable") });
}

export function useEmailOtpConfirm() {
  return useTwoFactorMutation((code: string) => api.post<void>("/auth/2fa/email/confirm", { code }));
}

export function useEmailOtpDisable() {
  return useTwoFactorMutation((code_or_password: string) =>
    api.post<void>("/auth/2fa/email/disable", { code_or_password }),
  );
}

/** Admin: `POST /admin/users/{id}/2fa/reset` (403 on self). */
export function useAdminReset2fa() {
  return useMutation({
    mutationFn: (userId: number) => api.post<void>(`/admin/users/${userId}/2fa/reset`),
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
