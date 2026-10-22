import { describe, expect, it } from "vitest";

import { ALL_NAV_ITEMS, NAV_GROUPS, filterNavItems, NAV_LABELS, SETTINGS_ITEM } from "@/components/shell/navItems";

describe("navItems", () => {
  it("groups Library and Operations, with Settings pinned outside both", () => {
    expect(NAV_GROUPS.map((g) => g.label)).toEqual(["Library", "Operations"]);
    expect(NAV_GROUPS.every((g) => g.items.every((item) => item.to !== SETTINGS_ITEM.to))).toBe(true);
  });

  it("marks only Printer as feature-gated", () => {
    const gated = NAV_GROUPS.flatMap((g) => g.items).filter((item) => item.featureGated);
    expect(gated.map((item) => item.to)).toEqual(["/printer"]);
  });

  it("flattens to every item including Settings, and builds a path->label map", () => {
    expect(ALL_NAV_ITEMS.at(-1)).toBe(SETTINGS_ITEM);
    expect(NAV_LABELS["/"]).toBe("Library");
    expect(NAV_LABELS["/settings"]).toBe("Settings");
  });

  it("hides perm-gated items the user lacks", () => {
    const all = NAV_GROUPS.flatMap((g) => g.items);
    const items = filterNavItems(all, { printerEnabled: true, can: () => false });
    expect(items.map((i) => i.to)).not.toContain("/duplicates");
    expect(items.map((i) => i.to)).toContain("/queue");
    const granted = filterNavItems(all, { printerEnabled: true, can: () => true });
    expect(granted.map((i) => i.to)).toContain("/duplicates");
  });

  it("hides feature-gated items when the printer is disabled", () => {
    const all = NAV_GROUPS.flatMap((g) => g.items);
    const items = filterNavItems(all, { printerEnabled: false, can: () => true });
    expect(items.map((i) => i.to)).not.toContain("/printer");
  });
});
