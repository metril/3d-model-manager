import { useTranslation } from "react-i18next";

/** Translates a nav label (`nav.<English label>`); dynamic labels such as a
 * printer or external-link name have no entry and pass through unchanged. */
export function useNavLabel(): (label: string) => string {
  const { t } = useTranslation();
  return (label) => t(`nav.${label}`, { defaultValue: label });
}
