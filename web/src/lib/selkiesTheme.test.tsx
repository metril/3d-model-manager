import { act, render } from "@testing-library/react";
import { ThemeProvider, useTheme } from "next-themes";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { SelkiesThemeBridge } from "@/lib/SelkiesThemeBridge";
import { APP_THEME_KEY, migrateThemeKey, syncSelkiesTheme, useFollowSelkiesTheme } from "@/lib/selkiesTheme";

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
  it("does not click a toggle whose state is unreadable", () => {
    const doc = fakeDoc("");
    expect(syncSelkiesTheme(doc, "dark")).toBe("missing");
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

  function mount(withFollow: boolean) {
    const out = { resolved: "", pref: "" };
    function Probe() {
      const t = useTheme();
      out.resolved = t.resolvedTheme ?? "";
      out.pref = t.theme ?? "";
      return null;
    }
    function Follow() {
      useFollowSelkiesTheme();
      return null;
    }
    render(
      <ThemeProvider attribute="class" storageKey={APP_THEME_KEY} defaultTheme="system" enableSystem>
        <SelkiesThemeBridge />
        {withFollow ? <Follow /> : null}
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
    mount(false);
    expect(localStorage.getItem("theme")).toBe("light");
  });

  it("follows Selkies' toggle where the frame is mounted", () => {
    localStorage.setItem(APP_THEME_KEY, "light");
    const out = mount(true);
    selkiesWrote("dark");
    expect(out.resolved).toBe("dark");
    expect(localStorage.getItem(APP_THEME_KEY)).toBe("dark");
  });

  it("ignores theme events in a document without the slicer frame", () => {
    localStorage.setItem(APP_THEME_KEY, "light");
    const out = mount(false);
    selkiesWrote("dark");
    expect(out.resolved).toBe("light");
    expect(localStorage.getItem(APP_THEME_KEY)).toBe("light");
  });

  it("keeps a system preference when Selkies reports the already-resolved theme", () => {
    const out = mount(true);
    expect(out.pref).toBe("system");
    selkiesWrote("light"); // matchMedia stub resolves system to light
    expect(out.pref).toBe("system");
  });
});
