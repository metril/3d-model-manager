import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ShortcutsDialog } from "@/components/shell/ShortcutsDialog";

describe("ShortcutsDialog", () => {
  it("lists the digit-jump and help shortcuts when open", () => {
    render(<ShortcutsDialog open onOpenChange={() => {}} />);
    expect(screen.getByText("Keyboard shortcuts")).toBeInTheDocument();
    expect(screen.getByText("Jump to the first nine sidebar items")).toBeInTheDocument();
    expect(screen.getByText("1 – 9")).toBeInTheDocument();
    expect(screen.getByText("Focus the library search")).toBeInTheDocument();
  });

  it("renders nothing when closed and closes on Escape", () => {
    const { rerender } = render(<ShortcutsDialog open={false} onOpenChange={() => {}} />);
    expect(screen.queryByText("Keyboard shortcuts")).toBeNull();
    const onOpenChange = vi.fn();
    rerender(<ShortcutsDialog open onOpenChange={onOpenChange} />);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
