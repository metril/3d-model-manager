import { useTranslation } from "react-i18next";

/** "Seeded from environment (ENV_NAME)" under a field that still holds its env-seeded value. */
export function SeededHint({ field, seeded }: { field: string; seeded: Record<string, string> }) {
  const { t } = useTranslation();
  const env = seeded[field];
  if (!env) return null;
  return <p className="text-xs text-muted-foreground">{t("settings.server.seeded", { env })}</p>;
}
