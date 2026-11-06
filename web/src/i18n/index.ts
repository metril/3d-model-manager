import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";

/** Languages the UI ships. To contribute one: add `locales/<code>.json`
 * (copy en.json), register it here, and run `npm run i18n:check`. */
export const LANGUAGES: Record<string, string> = { en: "English" };
export const LANGUAGE_KEY = "tdmm.lang";

function syncHtmlLang(lng: string | undefined) {
  if (lng && typeof document !== "undefined") document.documentElement.lang = lng;
}

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: { en: { translation: en } },
    fallbackLng: "en",
    supportedLngs: Object.keys(LANGUAGES),
    nonExplicitSupportedLngs: true,
    interpolation: { escapeValue: false },
    detection: { order: ["localStorage", "navigator"], lookupLocalStorage: LANGUAGE_KEY, caches: ["localStorage"] },
  });

i18n.on("languageChanged", syncHtmlLang);
syncHtmlLang(i18n.resolvedLanguage ?? i18n.language);

export default i18n;
