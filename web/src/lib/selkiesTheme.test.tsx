import { act, render } from "@testing-library/react";
import { ThemeProvider, useTheme } from "next-themes";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { APP_THEME_KEY, migrateThemeKey, SelkiesThemeBridge, syncSelkiesTheme } from "@/lib/selkiesTheme";

function fakeDoc(cls: string | null) {
  const doc = document.implementation.createHTMLDocument("x");
  if (cls !== null) {
    const el = doc.createElement("div");
    el.className = `theme-toggle ${cls}`;
    el.click = vi.fn();
    doc.body.appendChild(el);
  }
  return doc;
}

describe("syncSelkiesTheme", () => {
  it("clicks the toggle when the dashboard differs", () => {
    const doc = fakeDoc("dark");
    expect(syncSelkiesTheme(doc, "light")).toBe("clicked");
    expect((doc.querySelector(".theme-toggle") as HTMLElement).click).toHaveBeenCalledTimes(1);
  });
  it("does nothing when already matching", () => {
    const doc = fakeDoc("light");
    expect(syncSelkiesTheme(doc, "light")).toBe("noop");
    expect((doc.querySelector(".theme-toggle") as HTMLElement).click).not.toHaveBeenCalled();
  });
  it("reports missing when there is no toggle or document", () => {
    expect(syncSelkiesTheme(fakeDoc(null), "dark")).toBe("missing");
    expect(syncSelkiesTheme(null, "dark")).toBe("missing");
  });
});

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

  it("mirrors the resolved theme into Selkies' key and follows its storage event", () => {
    localStorage.setItem(APP_THEME_KEY, "light");
    let current = "";
    function Probe() {
      current = useTheme().resolvedTheme ?? "";
      return null;
    }
    render(
      <ThemeProvider attribute="class" storageKey={APP_THEME_KEY} defaultTheme="system" enableSystem>
        <SelkiesThemeBridge />
        <Probe />
      </ThemeProvider>,
    );
    expect(localStorage.getItem("theme")).toBe("light");
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: "theme", newValue: "dark" }));
    });
    expect(current).toBe("dark");
    expect(localStorage.getItem(APP_THEME_KEY)).toBe("dark");
    expect(localStorage.getItem("theme")).toBe("dark");
  });
});
