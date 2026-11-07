import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OverflowMenu } from "@/components/ui/overflow-menu";

function open(name: RegExp | string = /more actions/i) {
  fireEvent.pointerDown(screen.getByRole("button", { name }), { button: 0 });
}

describe("OverflowMenu", () => {
  it("renders labeled groups, skips hidden items and empty groups, fires onSelect", async () => {
    const run = vi.fn();
    render(
      <OverflowMenu
        groups={[
          { label: "File", items: [{ id: "a", label: "Alpha", onSelect: run }, { id: "b", label: "Beta", hidden: true }] },
          { label: "Empty", items: [{ id: "c", label: "Gamma", hidden: true }] },
          { label: "Danger", items: [{ id: "d", label: "Remove", destructive: true }] },
        ]}
      />,
    );
    open();
    expect(await screen.findByText("File")).toBeInTheDocument();
    expect(screen.getByText("Danger")).toBeInTheDocument();
    expect(screen.queryByText("Empty")).toBeNull();
    expect(screen.queryByText("Beta")).toBeNull();
    expect(screen.getByRole("menuitem", { name: "Remove" })).toHaveAttribute("data-variant", "destructive");
    fireEvent.click(screen.getByRole("menuitem", { name: "Alpha" }));
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("uses a custom aria-label and renders nothing when every item is hidden", () => {
    const { rerender } = render(<OverflowMenu ariaLabel="Row menu" groups={[{ items: [{ id: "a", label: "A" }] }]} />);
    expect(screen.getByRole("button", { name: "Row menu" })).toBeInTheDocument();
    rerender(<OverflowMenu groups={[{ items: [{ id: "a", label: "A", hidden: true }] }]} />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});
