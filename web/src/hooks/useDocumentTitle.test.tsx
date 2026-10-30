import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PrinterOverviewItem } from "@/api/types";
import { printTitle, usePrintTitle } from "@/hooks/useDocumentTitle";

const item = (name: string, state: string, mins: number | null) =>
  ({ printer: { name }, status: { gcode_state: state, mc_remaining_time: mins } }) as unknown as PrinterOverviewItem;

function Probe({ items }: { items: PrinterOverviewItem[] }) {
  usePrintTitle(items);
  return null;
}

describe("usePrintTitle", () => {
  it("picks the soonest-finishing running printer", () => {
    expect(printTitle([item("A", "RUNNING", 83), item("B", "RUNNING", 20), item("C", "IDLE", 5)])).toBe(
      "20m · B · 3D Model Manager",
    );
    expect(printTitle([item("A", "IDLE", 5)])).toBeNull();
  });

  it("sets and restores document.title", () => {
    document.title = "Orig";
    const { rerender, unmount } = render(<Probe items={[item("A", "RUNNING", 83)]} />);
    expect(document.title).toBe("1h 23m · A · 3D Model Manager");
    rerender(<Probe items={[]} />);
    expect(document.title).toBe("Orig");
    rerender(<Probe items={[item("A", "RUNNING", 10)]} />);
    unmount();
    expect(document.title).toBe("Orig");
  });
});
