import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatTile } from "@/components/ui/stat-tile";

describe("StatTile", () => {
  it("renders the label in label-caps styling", () => {
    render(<StatTile label="Printing now" value={3} />);
    const label = screen.getByText("Printing now");
    expect(label.className).toContain("label-caps");
  });

  it("renders the unit muted, separate from the value", () => {
    render(<StatTile label="Filament used" value={128} unit="g" />);
    const unit = screen.getByText("g");
    expect(unit.className).toContain("text-muted-foreground");
    expect(screen.getByText("128")).toBeInTheDocument();
  });

  it("renders children (e.g. a Sparkline) below the value", () => {
    render(
      <StatTile label="Jobs" value={5}>
        <span data-testid="child">chart</span>
      </StatTile>,
    );
    expect(screen.getByTestId("child")).toBeInTheDocument();
  });

  it("renders an optional delta", () => {
    render(<StatTile label="Jobs" value={5} delta="+2" />);
    expect(screen.getByText("+2")).toBeInTheDocument();
  });
});
