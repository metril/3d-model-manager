/** Query/mutation hooks for `/api/notifications` (Q6). */
import { keepPreviousData, queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

export type NotificationStatus = "sent" | "failed" | "queued" | "digested" | "quiet";

export interface NotificationField {
  name: string;
  label: string;
  type: "text" | "password" | "number" | "select";
  secret: boolean;
  required: boolean;
  placeholder: string | null;
  default: string | null;
  options: string[] | null;
  multiline?: boolean;
  help?: string | null;
}
export interface NotificationKind {
  kind: string;
  label: string;
  supports_photo: boolean;
  fields: NotificationField[];
}
export interface NotificationEvent {
  id: string;
  label: string;
  description: string;
}
export interface NotificationCatalog {
  events: NotificationEvent[];
  kinds: NotificationKind[];
  template_vars: string[];
}
export interface QuietHours {
  start: string;
  end: string;
  tz: string;
}
export interface NotificationChannel {
  id: number;
  kind: string;
  name: string;
  config: Record<string, string>;
  enabled: boolean;
  events: string[];
  quiet_hours: QuietHours | null;
  digest_cron: string | null;
  attach_photo: boolean;
  created_at: string;
  updated_at: string;
}
export interface ChannelIn {
  kind: string;
  name: string;
  config: Record<string, string>;
  enabled?: boolean;
  events?: string[];
  quiet_hours?: QuietHours | null;
  digest_cron?: string | null;
  attach_photo?: boolean;
}
export type ChannelPatch = Partial<Omit<ChannelIn, "kind">>;
export interface NotificationTemplate {
  id: number;
  event: string;
  title_tpl: string;
  body_tpl: string;
}
export type TemplateDefaults = Record<string, { title_tpl: string; body_tpl: string }>;
export interface TemplatePreview {
  title: string;
  body: string;
  error: string | null;
}
export interface NotificationLogItem {
  id: number;
  channel_id: number | null;
  channel_name: string | null;
  event: string;
  printer_id: number | null;
  status: NotificationStatus;
  error: string | null;
  sent_at: string;
}
export interface NotificationLogPage {
  items: NotificationLogItem[];
  total: number;
}

const BASE = "/notifications";
const channelsKey = ["notifications", "channels"] as const;
const templatesKey = ["notifications", "templates"] as const;

export function useNotificationCatalog() {
  return useQuery(
    queryOptions({
      queryKey: ["notifications", "events"] as const,
      queryFn: () => api.get<NotificationCatalog>(`${BASE}/events`),
    }),
  );
}

export function useChannels() {
  return useQuery({ queryKey: channelsKey, queryFn: () => api.get<NotificationChannel[]>(`${BASE}/channels`) });
}

export function useCreateChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ChannelIn) => api.post<NotificationChannel>(`${BASE}/channels`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: channelsKey }),
  });
}

export function useUpdateChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: ChannelPatch }) =>
      api.patch<NotificationChannel>(`${BASE}/channels/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: channelsKey }),
  });
}

export function useDeleteChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`${BASE}/channels/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: channelsKey }),
  });
}

export function useTestChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post<{ ok: boolean; error: string | null }>(`${BASE}/channels/${id}/test`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications", "log"] }),
  });
}

export function useTemplates() {
  return useQuery({ queryKey: templatesKey, queryFn: () => api.get<NotificationTemplate[]>(`${BASE}/templates`) });
}

export function useTemplateDefaults() {
  return useQuery({
    queryKey: ["notifications", "template-defaults"] as const,
    queryFn: () => api.get<TemplateDefaults>(`${BASE}/templates/defaults`),
  });
}

export function useCreateTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { event: string; title_tpl: string; body_tpl: string }) =>
      api.post<NotificationTemplate>(`${BASE}/templates`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: templatesKey }),
  });
}

export function useUpdateTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: { title_tpl?: string; body_tpl?: string } }) =>
      api.patch<NotificationTemplate>(`${BASE}/templates/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: templatesKey }),
  });
}

export function useDeleteTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`${BASE}/templates/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: templatesKey }),
  });
}

export function usePreviewTemplate() {
  return useMutation({
    mutationFn: (body: { event: string; title_tpl: string; body_tpl: string }) =>
      api.post<TemplatePreview>(`${BASE}/templates/preview`, body),
  });
}

export function useNotificationLog(params: { status: string; limit: number; offset: number }) {
  const qs = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
  if (params.status) qs.set("status", params.status);
  return useQuery({
    queryKey: ["notifications", "log", params] as const,
    queryFn: () => api.get<NotificationLogPage>(`${BASE}/log?${qs.toString()}`),
    placeholderData: keepPreviousData,
  });
}

export interface EmailPrefs {
  notify_print_start: boolean;
  notify_print_complete: boolean;
  notify_print_failed: boolean;
  notify_print_stopped: boolean;
}
export interface EmailPrefsOut extends EmailPrefs {
  smtp_configured: boolean;
  has_email: boolean;
}

const emailPrefsKey = ["user-notifications", "preferences"] as const;

export function useEmailPrefs() {
  return useQuery({
    queryKey: emailPrefsKey,
    queryFn: () => api.get<EmailPrefsOut>("/user-notifications/preferences"),
  });
}

export function useUpdateEmailPrefs() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: EmailPrefs) => api.put<EmailPrefsOut>("/user-notifications/preferences", body),
    onSuccess: (data) => qc.setQueryData(emailPrefsKey, data),
  });
}
