import { act, render } from "@testing-library/react";
import { ThemeProvider, useTheme } from "next-themes";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

import { SelkiesThemeBridge } from "@/lib/SelkiesThemeBridge";
import { APP_THEME_KEY, migrateThemeKey } from "@/lib/selkiesTheme";

describe("theme key", () => {
  beforeAll(() => {
    window.matchMedia = ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false })) as unknown as typeof window.matchMedia;
  });
  beforeEach(() => localStorage.clear());

  it("migrates the legacy shared key once", () => {
    localStorage.setItem("theme", "system");
    migrateThemeKey();
    expect(localStorage.getItem(APP_THEME_KEY)).toBe("system");
    localStorage.setItem("theme", "dark");
    migrateThemeKey();
    expect(localStorage.getItem(APP_THEME_KEY)).toBe("system");
  });

  function mount() {
    const out = { resolved: "", pref: "" };
    function Probe() {
      const t = useTheme();
      out.resolved = t.resolvedTheme ?? "";
      out.pref = t.theme ?? "";
      return null;
    }
    render(
      <ThemeProvider attribute="class" storageKey={APP_THEME_KEY} defaultTheme="system" enableSystem>
        <SelkiesThemeBridge />
        <Probe />
      </ThemeProvider>,
    );
    return out;
  }
  const selkiesWrote = (v: string) =>
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: "theme", newValue: v }));
    });

  it("mirrors the resolved theme into Selkies' key", () => {
    localStorage.setItem(APP_THEME_KEY, "light");
    mount();
    expect(localStorage.getItem("theme")).toBe("light");
  });

  it("follows Selkies' toggle from the stream tab", () => {
    localStorage.setItem(APP_THEME_KEY, "light");
    const out = mount();
    selkiesWrote("dark");
    expect(out.resolved).toBe("dark");
    expect(localStorage.getItem(APP_THEME_KEY)).toBe("dark");
  });

  it("keeps a system preference when Selkies reports the already-resolved theme", () => {
    const out = mount();
    expect(out.pref).toBe("system");
    selkiesWrote("light"); // matchMedia stub resolves system to light
    expect(out.pref).toBe("system");
  });
});
