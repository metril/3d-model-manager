import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import type { NotificationCatalog, NotificationChannel } from "@/api/notifications";

export const catalog: NotificationCatalog = {
  events: [
    { id: "print_done", label: "Print finished", description: "A print completed" },
    { id: "print_failed", label: "Print failed", description: "A print failed" },
  ],
  kinds: [
    {
      kind: "ntfy",
      label: "ntfy",
      supports_photo: true,
      fields: [
        { name: "url", label: "Server URL", type: "text", secret: false, required: true, placeholder: null, default: "https://ntfy.sh", options: null },
        { name: "token", label: "Token", type: "password", secret: true, required: false, placeholder: null, default: null, options: null },
      ],
    },
    {
      kind: "webhook",
      label: "Webhook",
      supports_photo: false,
      fields: [
        { name: "url", label: "URL", type: "text", secret: false, required: true, placeholder: null, default: null, options: null },
      ],
    },
  ],
  template_vars: ["printer", "model"],
};

export function fakeChannel(o: Partial<NotificationChannel> = {}): NotificationChannel {
  return {
    id: 1,
    kind: "ntfy",
    name: "Phone",
    config: { url: "https://ntfy.sh", token: "********" },
    enabled: true,
    events: ["print_done"],
    quiet_hours: null,
    digest_cron: null,
    attach_photo: false,
    created_at: "2026-10-23T00:00:00Z",
    updated_at: "2026-10-23T00:00:00Z",
    ...o,
  };
}

export function renderWithQuery(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}
