import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppearanceCard } from "@/components/settings/AppearanceCard";
import { AppearanceProvider } from "@/lib/appearance";

const { setThemeMock } = vi.hoisted(() => ({ setThemeMock: vi.fn() }));
vi.mock("next-themes", () => ({ useTheme: () => ({ theme: "dark", setTheme: setThemeMock }) }));

describe("AppearanceCard", () => {
  beforeEach(() => {
    window.localStorage.clear();
    setThemeMock.mockClear();
  });

  it("switches mode, style and accent", () => {
    render(
      <AppearanceProvider>
        <AppearanceCard />
      </AppearanceProvider>,
    );
    expect(screen.getByRole("button", { name: "dark" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "light" }));
    expect(setThemeMock).toHaveBeenCalledWith("light");

    fireEvent.click(screen.getByRole("button", { name: "vibrant" }));
    expect(document.documentElement.dataset.style).toBe("vibrant");

    fireEvent.click(screen.getByRole("button", { name: "purple" }));
    expect(document.documentElement.dataset.accent).toBe("purple");
    expect(window.localStorage.getItem("tdmm-accent")).toBe("purple");
  });
});
