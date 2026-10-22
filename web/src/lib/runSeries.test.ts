import { describe, expect, it } from "vitest";

import type { ArchiveOut } from "@/api/types";
import { dailyRunCounts } from "@/lib/runSeries";

function archive(createdAt: string): ArchiveOut {
  return {
    id: 1,
    print_job_id: 1,
    printer_id: 1,
    printer_name: "A1",
    origin: "app",
    subtask_name: null,
    model_id: null,
    model_slug: null,
    model_name: null,
    revision_id: null,
    result: "completed",
    duration_s: null,
    est_filament: null,
    plate: null,
    fetch_state: "done",
    fetch_error: null,
    has_thumbnail: false,
    has_file: false,
    started_at: null,
    finished_at: null,
    created_at: createdAt,
    created_by_user_id: null,
    run: null,
  } as ArchiveOut;
}

describe("dailyRunCounts", () => {
  it("buckets by local day over the trailing window", () => {
    const now = new Date("2026-10-22T12:00:00");
    const archives = [
      archive("2026-10-22T08:00:00"),
      archive("2026-10-22T09:30:00"),
      archive("2026-10-21T23:59:00"),
      archive("2026-10-08T00:00:00"), // outside the 14-day window
    ];
    const counts = dailyRunCounts(archives, 14, now);
    expect(counts).toHaveLength(14);
    expect(counts[13]).toBe(2); // today
    expect(counts[12]).toBe(1); // yesterday
    expect(counts.reduce((a, b) => a + b, 0)).toBe(3);
  });

  it("returns a zero-filled series for an empty archive list", () => {
    const counts = dailyRunCounts([], 14, new Date("2026-10-22T12:00:00"));
    expect(counts).toEqual(Array(14).fill(0));
  });
});
