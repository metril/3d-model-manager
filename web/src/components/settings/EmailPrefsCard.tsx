/** Per-user email opt-ins for print events (T8a). Hidden until SMTP is configured. */
import { useTranslation } from "react-i18next";

import { useEmailPrefs, useUpdateEmailPrefs, type EmailPrefs } from "@/api/notifications";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const ROWS: { key: keyof EmailPrefs; label: string }[] = [
  { key: "notify_print_start", label: "emailPrefs.start" },
  { key: "notify_print_complete", label: "emailPrefs.complete" },
  { key: "notify_print_failed", label: "emailPrefs.failed" },
  { key: "notify_print_stopped", label: "emailPrefs.stopped" },
];

export function EmailPrefsCard() {
  const { t } = useTranslation();
  const prefs = useEmailPrefs();
  const update = useUpdateEmailPrefs();
  const data = prefs.data;
  if (!data?.smtp_configured) return null;

  function toggle(key: keyof EmailPrefs, value: boolean) {
    if (!data) return;
    update.mutate({
      notify_print_start: data.notify_print_start,
      notify_print_complete: data.notify_print_complete,
      notify_print_failed: data.notify_print_failed,
      notify_print_stopped: data.notify_print_stopped,
      [key]: value,
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("emailPrefs.title")}</CardTitle>
        <CardDescription>{t("emailPrefs.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {!data.has_email && <p className="text-sm text-muted-foreground">{t("emailPrefs.noEmail")}</p>}
        {ROWS.map(({ key, label }) => (
          <label key={key} className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={data[key]}
              disabled={update.isPending}
              onChange={(e) => toggle(key, e.target.checked)}
            />
            {t(label)}
          </label>
        ))}
        {update.isError && (
          <p role="alert" className="text-sm text-destructive">
            {t("emailPrefs.saveFailed")}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
