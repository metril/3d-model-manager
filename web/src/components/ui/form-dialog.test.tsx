import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";

function Harness({ dirty = false, busy = false }: { dirty?: boolean; busy?: boolean }) {
  const [open, setOpen] = useState(true);
  return (
    <>
      <p data-testid="outside">outside</p>
      <FormDialog open={open} onOpenChange={setOpen} dirty={dirty} busy={busy}>
        <DialogTitle>Edit thing</DialogTitle>
      </FormDialog>
    </>
  );
}

const isOpen = () => screen.queryByText("Edit thing") !== null;

describe("FormDialog", () => {
  it("ignores outside pointer-down and Escape", () => {
    render(<Harness />);
    fireEvent.pointerDown(document.body);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(isOpen()).toBe(true);
  });

  it("closes immediately via X when clean", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(isOpen()).toBe(false);
  });

  it("asks to discard when dirty, and Discard closes", () => {
    render(<Harness dirty />);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.getByText("Discard changes?")).toBeInTheDocument();
    expect(isOpen()).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(isOpen()).toBe(false);
  });

  it("Keep editing leaves the form open", () => {
    render(<Harness dirty />);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.queryByText("Discard changes?")).toBeNull();
    expect(isOpen()).toBe(true);
  });

  it("blocks closing while busy", () => {
    render(<Harness dirty busy />);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByText("Discard changes?")).toBeNull();
    expect(isOpen()).toBe(true);
  });
});
