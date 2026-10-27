import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DiffView } from "@/components/model-detail/DiffView";
import type { DiffResponse } from "@/api/types";

const DIFF: DiffResponse = {
  added: [{ rel_path: "new.stl", a: null, b: { blob_hash: "b1", size: 100 } }],
  removed: [{ rel_path: "old.stl", a: { blob_hash: "a1", size: 50 }, b: null }],
  changed: [
    {
      rel_path: "body.stl",
      a: { blob_hash: "a2", size: 200 },
      b: { blob_hash: "b2", size: 250 },
    },
  ],
  unchanged: [{ rel_path: "base.stl", a: { blob_hash: "c1", size: 10 }, b: { blob_hash: "c1", size: 10 } }],
};

describe("DiffView", () => {
  it("renders each rel_path under its classified section with a colored badge", () => {
    render(<DiffView diff={DIFF} />);

    expect(screen.getByText("new.stl")).toBeInTheDocument();
    expect(screen.getByText("old.stl")).toBeInTheDocument();
    expect(screen.getByText("body.stl")).toBeInTheDocument();
    expect(screen.getByText("base.stl")).toBeInTheDocument();

    const addedBadges = screen.getAllByText("Added");
    // one in the section heading count badge, one per-row badge
    expect(addedBadges.length).toBeGreaterThanOrEqual(1);
    expect(addedBadges.some((badge) => badge.className.includes("success"))).toBe(true);

    const removedBadges = screen.getAllByText("Removed");
    expect(removedBadges.some((badge) => badge.className.includes("destructive"))).toBe(true);

    const changedBadges = screen.getAllByText("Changed");
    expect(changedBadges.some((badge) => badge.className.includes("warning"))).toBe(true);

    const unchangedBadges = screen.getAllByText("Unchanged");
    expect(unchangedBadges.some((badge) => badge.className.includes("muted"))).toBe(true);
  });

  it("shows an empty-state message for sections with no entries", () => {
    render(
      <DiffView
        diff={{ added: [], removed: [], changed: [], unchanged: DIFF.unchanged }}
      />,
    );

    expect(screen.getAllByText("None")).toHaveLength(3);
  });
});
