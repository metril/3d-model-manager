import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TrayPicker } from "@/components/print/TrayPicker";

const TRAYS = [
  { slot: 0, label: "AMS 0·0", loaded: true },
  { slot: 1, label: "AMS 0·1", loaded: false },
  { slot: 2, label: "AMS 0·2", loaded: false },
];

describe("TrayPicker", () => {
  it("hides empty trays behind a show-all toggle", () => {
    render(<TrayPicker trays={TRAYS} selected={null} onChange={vi.fn()} ariaLabel="Tray" />);
    fireEvent.click(screen.getByRole("button", { name: "Show all trays (2 empty)" }));
    expect(screen.getByRole("button", { name: "Loaded spools only" })).toBeInTheDocument();
  });

  it("has no toggle when every tray is loaded, and keeps the current choice counted as shown", () => {
    const { rerender } = render(
      <TrayPicker trays={TRAYS.map((t) => ({ ...t, loaded: true }))} selected={null} onChange={vi.fn()} ariaLabel="Tray" />,
    );
    expect(screen.queryByRole("button", { name: /Show all trays/ })).toBeNull();
    rerender(<TrayPicker trays={TRAYS} selected={1} onChange={vi.fn()} ariaLabel="Tray" />);
    expect(screen.getByRole("button", { name: "Show all trays (1 empty)" })).toBeInTheDocument();
  });
});
