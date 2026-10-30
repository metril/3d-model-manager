import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { QueueEntry } from "@/api/types";
import { QueueFilamentChips } from "@/components/print/QueueFilaments";

function entry(over: Partial<QueueEntry>): QueueEntry {
  return { id: 1, model_id: 1, position: 1, added_at: "", ...over } as QueueEntry;
}

const f = (slot: number, over = {}) => ({
  slot,
  type: "PLA",
  color: "#FF0000",
  grams: 10,
  tray: slot - 1,
  spool_id: slot,
  spool_name: `Acme ${slot}`,
  spool_color: "#FF0000",
  colour_mismatch: false,
  ...over,
});

describe("QueueFilamentChips", () => {
  it("renders nothing without filaments", () => {
    const { container } = render(<QueueFilamentChips entry={entry({})} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows slot chips with the mapped spool and the estimate", () => {
    render(<QueueFilamentChips entry={entry({ filaments: [f(1)], estimated_cost: 2.5 })} />);
    expect(screen.getByTestId("queue-filament-1")).toHaveTextContent("Acme 1");
    expect(screen.getByText("Est. 2.50")).toBeInTheDocument();
    expect(screen.queryByText(/Colour mismatch/)).not.toBeInTheDocument();
  });

  it("warns naming the mismatched slot", () => {
    render(
      <QueueFilamentChips
        entry={entry({ filaments: [f(1), f(2, { colour_mismatch: true, spool_color: "#0000FF" })] })}
      />,
    );
    const chip = screen.getByText(/Colour mismatch \(slot 2\)/);
    expect(chip.closest("[title]")).toHaveAttribute("title", expect.stringContaining("Slot 2"));
  });
});
