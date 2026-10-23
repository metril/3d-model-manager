import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useVisibleNav } from "@/components/shell/useVisibleNav";

vi.mock("@/api/auth", () => ({ useAuth: () => ({ can: () => true }) }));
vi.mock("@/api/features", () => ({ useFeatures: () => ({ data: { printer_enabled: false } }) }));
vi.mock("@/api/finance", () => ({ useFinanceConfig: () => ({ data: { billing_enabled: false } }) }));
vi.mock("@/api/printers", () => ({ usePrinters: () => ({ data: [] }) }));
vi.mock("@/api/externalLinks", () => ({
  useExternalLinks: () => ({
    data: [{ id: 3, name: "Wiki", url: "https://w", icon: null, sort_order: 0, open_mode: "iframe" }],
  }),
}));

describe("useVisibleNav", () => {
  it("orders groups, then external links, then System (sidebar order)", () => {
    const { result } = renderHook(() => useVisibleNav());
    const labels = result.current.ordered.map((i) => i.label);
    expect(labels.indexOf("Spools")).toBeLessThan(labels.indexOf("Wiki"));
    expect(labels.indexOf("Wiki")).toBeLessThan(labels.indexOf("Background jobs"));
    expect(labels.at(-1)).toBe("Settings");
  });
});
