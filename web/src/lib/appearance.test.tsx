import { act, render, renderHook, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AppearanceProvider } from "@/lib/appearance";
import { ACCENT_KEY, STYLE_KEY, useAppearance } from "@/lib/appearanceCore";

const wrapper = ({ children }: { children: ReactNode }) => <AppearanceProvider>{children}</AppearanceProvider>;

describe("appearance", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => {
    delete document.documentElement.dataset.style;
    delete document.documentElement.dataset.accent;
  });

  it("defaults to classic/orange and stamps <html>", () => {
    const { result } = renderHook(() => useAppearance(), { wrapper });
    expect(result.current.style).toBe("classic");
    expect(result.current.accent).toBe("orange");
    expect(document.documentElement.dataset.style).toBe("classic");
    expect(document.documentElement.dataset.accent).toBe("orange");
  });

  it("persists choices and restores them on remount", () => {
    const first = renderHook(() => useAppearance(), { wrapper });
    act(() => {
      first.result.current.setStyle("glow");
      first.result.current.setAccent("teal");
    });
    expect(window.localStorage.getItem(STYLE_KEY)).toBe("glow");
    expect(window.localStorage.getItem(ACCENT_KEY)).toBe("teal");
    expect(document.documentElement.dataset.accent).toBe("teal");
    first.unmount();

    const second = renderHook(() => useAppearance(), { wrapper });
    expect(second.result.current.style).toBe("glow");
    expect(second.result.current.accent).toBe("teal");
  });

  it("ignores garbage in storage", () => {
    window.localStorage.setItem(STYLE_KEY, "neon");
    window.localStorage.setItem(ACCENT_KEY, "pink");
    const { result } = renderHook(() => useAppearance(), { wrapper });
    expect([result.current.style, result.current.accent]).toEqual(["classic", "orange"]);
  });

  it("throws outside the provider", () => {
    function Probe() {
      useAppearance();
      return <p>ok</p>;
    }
    expect(() => render(<Probe />)).toThrow();
    expect(screen.queryByText("ok")).toBeNull();
  });
});
