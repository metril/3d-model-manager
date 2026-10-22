import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StateBadge } from "@/components/ui/state-badge";

describe("StateBadge", () => {
  it("renders the given label", () => {
    render(<StateBadge tone="printing" label="Printing" />);
    expect(screen.getByText("Printing")).toBeInTheDocument();
  });

  it("uses bg-state-failed for tone=failed", () => {
    const { container } = render(<StateBadge tone="failed" label="Failed" />);
    const dot = container.querySelector('[data-slot="state-badge-dot"]');
    expect(dot?.className).toContain("bg-state-failed");
  });

  it("uses bg-state-printing for tone=printing", () => {
    const { container } = render(<StateBadge tone="printing" label="Printing" />);
    const dot = container.querySelector('[data-slot="state-badge-dot"]');
    expect(dot?.className).toContain("bg-state-printing");
  });

  it("applies a pulse animation class when pulse is true", () => {
    const { container } = render(<StateBadge tone="printing" label="Printing" pulse />);
    const dot = container.querySelector('[data-slot="state-badge-dot"]');
    expect(dot?.className).toContain("animate-pulse");
  });

  it("renders the label in label-caps text", () => {
    render(<StateBadge tone="idle" label="Idle" />);
    const label = screen.getByText("Idle");
    expect(label.className).toContain("label-caps");
  });
});
