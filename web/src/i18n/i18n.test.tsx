import { render, screen, act } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useTranslation } from "react-i18next";

import i18n, { LANGUAGES, LANGUAGE_KEY } from "@/i18n";

function Probe() {
  const { t } = useTranslation();
  return <p>{t("stats.title")}</p>;
}

afterEach(async () => {
  localStorage.clear();
  await i18n.changeLanguage("en");
});

describe("i18n", () => {
  it("initialises with en and resolves keys", () => {
    render(<Probe />);
    expect(screen.getByText("Stats")).toBeInTheDocument();
    expect(i18n.resolvedLanguage).toBe("en");
    expect(Object.keys(LANGUAGES)).toEqual(["en"]);
  });

  it("switches language, persists the choice and falls back to en for missing keys", async () => {
    i18n.addResourceBundle("xx", "translation", { stats: { title: "Estadisticas" } });
    render(<Probe />);
    await act(async () => {
      await i18n.changeLanguage("xx");
    });
    expect(screen.getByText("Estadisticas")).toBeInTheDocument();
    expect(i18n.t("settings.title")).toBe("Settings");
    expect(localStorage.getItem(LANGUAGE_KEY)).toBe("xx");
    i18n.removeResourceBundle("xx", "translation");
  });
});
