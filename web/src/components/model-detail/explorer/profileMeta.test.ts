import { describe, expect, it } from "vitest";

import type { FileOut } from "@/api/types";
import { profileMetaLine } from "./fileMeta";

const base = { id: 1 } as FileOut;

describe("profileMetaLine", () => {
  it("lists printers and plate count", () => {
    const f = {
      ...base,
      source_meta: {
        compat: ["P1S", "A1 mini", "X1", "H2D"].map((p) => ({ model: null, product: p, nozzle: 0.4 })),
        plates: [{ index: 1 }, { index: 2 }],
      },
    } as unknown as FileOut;
    expect(profileMetaLine(f)).toBe("Printers: P1S, A1 mini, X1 +1 · 2 plates");
  });
  it("is null without source_meta", () => {
    expect(profileMetaLine(base)).toBeNull();
  });
});
