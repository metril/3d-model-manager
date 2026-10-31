import { describe, expect, it } from "vitest";

import { Printer } from "lucide-react";

import type { PrinterOut } from "@/api/types";
import {
  ALL_NAV_ITEMS,
  NAV_GROUPS,
  NAV_LABELS,
  SYSTEM_ITEMS,
  filterNavGroups,
  filterNavItems,
  printerNavItems,
} from "@/components/shell/navItems";

function fakePrinter(overrides: Partial<PrinterOut> = {}): PrinterOut {
  return {
    id: 1,
    name: "Bambu",
    kind: "bambu_lan",
    host: "10.0.0.5",
    serial: "ABC123",
    model: null,
    enabled: true,
    options: {},
    access_code_set: true,
    build_volume_mm: null,
    ...overrides,
  };
}

describe("navItems", () => {
  it("groups Print, Library, Inventory in order, with System pinned outside every group", () => {
    expect(NAV_GROUPS.map((g) => g.label)).toEqual(["Print", "Library", "Inventory"]);
    expect(NAV_GROUPS.every((g) => g.items.every((item) => !SYSTEM_ITEMS.some((s) => s.to === item.to)))).toBe(
      true,
    );
    expect(ALL_NAV_ITEMS.slice(-SYSTEM_ITEMS.length)).toEqual(SYSTEM_ITEMS);
  });

  it("marks the whole Print group feature-gated, nothing else", () => {
    const printGroup = NAV_GROUPS.find((g) => g.label === "Print");
    expect(printGroup?.featureGated).toBe(true);
    expect(NAV_GROUPS.filter((g) => g.label !== "Print").every((g) => !g.featureGated)).toBe(true);
  });

  it("flattens to every item including System, and builds a path->label map", () => {
    expect(NAV_LABELS["/"]).toBe("Dashboard");
    expect(NAV_LABELS["/library"]).toBe("Models");
    expect(NAV_LABELS["/settings"]).toBe("Settings");
    expect(NAV_LABELS["/system/jobs"]).toBe("Background jobs");
  });

  it("every ALL_NAV_ITEMS path is unique and has a NAV_LABELS entry", () => {
    const paths = ALL_NAV_ITEMS.map((item) => item.to as string);
    expect(new Set(paths).size).toBe(paths.length);
    for (const path of paths) {
      expect(NAV_LABELS[path]).toBeDefined();
    }
  });

  it("builds one printer nav item per printer, linking to /printers/$id with its id param", () => {
    const items = printerNavItems([fakePrinter({ id: 7, name: "Ender" }), fakePrinter({ id: 8, name: "Bambu" })]);
    expect(items).toEqual([
      { to: "/printers/$id", label: "Ender", icon: Printer, featureGated: true, translate: false, params: { id: "7" } },
      { to: "/printers/$id", label: "Bambu", icon: Printer, featureGated: true, translate: false, params: { id: "8" } },
    ]);
  });

  it("printerNavItems returns an empty list for no printers", () => {
    expect(printerNavItems([])).toEqual([]);
    expect(printerNavItems(undefined)).toEqual([]);
  });

  describe("filterNavItems", () => {
    it("hides perm-gated items the user lacks", () => {
      const all = NAV_GROUPS.flatMap((g) => g.items);
      const items = filterNavItems(all, { printerEnabled: true, can: () => false });
      expect(items.map((i) => i.to)).not.toContain("/duplicates");
      expect(items.map((i) => i.to)).not.toContain("/add");
      expect(items.map((i) => i.to)).not.toContain("/inventory/materials");
      expect(items.map((i) => i.to)).toContain("/print/queue");
      const granted = filterNavItems(all, { printerEnabled: true, can: () => true });
      expect(granted.map((i) => i.to)).toContain("/duplicates");
      expect(granted.map((i) => i.to)).toContain("/inventory/materials");
    });

    // Fix-review finding 9: Dashboard ("/") must disappear under the same
    // condition `redirectForIndex` (routes.tsx) redirects "/" away from --
    // no visible printer and no settings:write -- so the nav never offers
    // a link that would just bounce to /library.
    it("hides Dashboard when there's no visible printer and the user lacks settings:write", () => {
      const all = NAV_GROUPS.flatMap((g) => g.items);
      const items = filterNavItems(all, { printerEnabled: true, can: () => false, hasPrinter: false });
      expect(items.map((i) => i.to)).not.toContain("/");
    });

    it("keeps Dashboard when the user has no visible printer but holds settings:write", () => {
      const all = NAV_GROUPS.flatMap((g) => g.items);
      const items = filterNavItems(all, {
        printerEnabled: true,
        can: (p) => p === "settings:write",
        hasPrinter: false,
      });
      expect(items.map((i) => i.to)).toContain("/");
    });

    it("keeps Dashboard when the user has a visible printer, settings:write or not", () => {
      const all = NAV_GROUPS.flatMap((g) => g.items);
      const items = filterNavItems(all, { printerEnabled: true, can: () => false, hasPrinter: true });
      expect(items.map((i) => i.to)).toContain("/");
    });

    it("defaults hasPrinter to true (keeps Dashboard) when the option is omitted", () => {
      const all = NAV_GROUPS.flatMap((g) => g.items);
      const items = filterNavItems(all, { printerEnabled: true, can: () => false });
      expect(items.map((i) => i.to)).toContain("/");
    });
  });

  describe("filterNavGroups", () => {
    it("drops the Print group entirely when the printer feature is off", () => {
      const groups = filterNavGroups(NAV_GROUPS, { printerEnabled: false, can: () => true });
      expect(groups.map((g) => g.label)).toEqual(["Library", "Inventory"]);
    });

    it("keeps Print when the printer feature is on", () => {
      const groups = filterNavGroups(NAV_GROUPS, { printerEnabled: true, can: () => true });
      expect(groups.map((g) => g.label)).toEqual(["Print", "Library", "Inventory"]);
    });

    it("hides perm-gated items without dropping their group when siblings remain", () => {
      const groups = filterNavGroups(NAV_GROUPS, { printerEnabled: true, can: () => false });
      const library = groups.find((g) => g.label === "Library");
      expect(library?.items.map((i) => i.to)).toEqual(["/library", "/collections", "/projects", "/trash"]);
      const inventory = groups.find((g) => g.label === "Inventory");
      expect(inventory?.items.map((i) => i.to)).toEqual(["/inventory/spools", "/inventory/profiles"]);
    });
  });
});

describe("studio nav entry", () => {
  const base = { printerEnabled: true, can: () => true };
  const has = (opts: { studioEnabled?: boolean }) =>
    filterNavGroups(NAV_GROUPS, { ...base, ...opts }).some((g) => g.items.some((i) => i.to === "/studio"));
  it("sits under Print and only shows when Studio is enabled", () => {
    expect(NAV_GROUPS.find((g) => g.label === "Print")?.items.some((i) => i.to === "/studio")).toBe(true);
    expect(has({})).toBe(false);
    expect(has({ studioEnabled: true })).toBe(true);
  });
});
