import { describe, expect, it } from "vitest";

import { archivesRefetchInterval } from "@/api/archives";
import type { ArchiveOut } from "@/api/types";

describe("archivesRefetchInterval", () => {
  it("polls while any run is pending", () => {
    expect(archivesRefetchInterval([{ fetch_state: "pending" } as ArchiveOut])).toBe(5000);
  });
  it("stops otherwise", () => {
    expect(archivesRefetchInterval([{ fetch_state: "done" } as ArchiveOut])).toBe(false);
    expect(archivesRefetchInterval(undefined)).toBe(false);
  });
});
