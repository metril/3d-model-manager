import { describe, expect, it } from "vitest";

import { dateGroupOf, groupByDate } from "@/lib/dateGroups";

const now = new Date(2026, 10, 15, 12, 0, 0); // Nov 15 2026, noon local
const ago = (days: number, hours = 0) => new Date(now.getTime() - days * 86_400_000 - hours * 3_600_000).toISOString();

describe("dateGroupOf", () => {
  it("buckets by calendar day", () => {
    expect(dateGroupOf(ago(0, 1), now)).toBe("Today");
    expect(dateGroupOf(ago(1), now)).toBe("Yesterday");
    expect(dateGroupOf(ago(3), now)).toBe("This week");
    expect(dateGroupOf(ago(15), now)).toBe("This month");
    expect(dateGroupOf(ago(60), now)).toBe("Older");
  });
  it("treats garbage as Older", () => {
    expect(dateGroupOf("nope", now)).toBe("Older");
  });
});

describe("groupByDate", () => {
  it("groups consecutive rows and keeps order", () => {
    const rows = [
      { id: 1, at: ago(0, 1) },
      { id: 2, at: ago(0, 2) },
      { id: 3, at: ago(1) },
      { id: 4, at: ago(90) },
    ];
    const g = groupByDate(rows, (r) => r.at, now);
    expect(g.map((x) => [x.label, x.rows.map((r) => r.id)])).toEqual([
      ["Today", [1, 2]],
      ["Yesterday", [3]],
      ["Older", [4]],
    ]);
  });
});
