import { useTranslation } from "react-i18next";

export function RoutePending() {
  const { t } = useTranslation();
  return (
    <div className="flex h-full min-h-40 items-center justify-center" role="status" aria-label={t("routePending.loading")}>
      <div className="size-6 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-primary" />
    </div>
  );
}
